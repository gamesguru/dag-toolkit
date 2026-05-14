#!/usr/bin/env bash
# scan-servers.sh — Run testmatrix against all known servers and update known-servers.json
#
# Usage: ./scan-servers.sh [OPTIONS] [server...]
#   With no server args, discovers all servers from the JSONL DAG dump
#   With server args, scans only the named servers
#
# Options:
#   --jsonl FILE     Path to JSONL file (default: auto-discover merged-*.jsonl in script dir)
#   --skip-scanned   Skip servers that already have scan_status in known-servers.json
#   --jobs N          Number of parallel workers (default: 20)
#   --timeout N       Per-server timeout in seconds (default: 30)
#
# Requires: testmatrix, jq, python3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KNOWN="$SCRIPT_DIR/known-servers.json"
TESTMATRIX="${TESTMATRIX:-$HOME/.local/bin/testmatrix}"
TIMEOUT="${TIMEOUT:-30}"
RESULTS_DIR="$SCRIPT_DIR/.cache/scan-results"
JSONL=""
SKIP_SCANNED=false
JOBS=20

mkdir -p "$RESULTS_DIR"

if ! command -v jq &>/dev/null; then
  echo "error: jq is required" >&2
  exit 1
fi
if [[ ! -x "$TESTMATRIX" ]]; then
  echo "error: testmatrix not found at $TESTMATRIX" >&2
  echo "  set TESTMATRIX=/path/to/testmatrix" >&2
  exit 1
fi

# Parse flags
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
  --jsonl)
    JSONL="$2"
    shift 2
    ;;
  --skip-scanned)
    SKIP_SCANNED=true
    shift
    ;;
  --jobs)
    JOBS="$2"
    shift 2
    ;;
  --timeout)
    TIMEOUT="$2"
    shift 2
    ;;
  *.jsonl)
    # Auto-detect JSONL files passed as positional args
    if [[ -f "$1" ]]; then
      JSONL="$1"
    else
      POSITIONAL+=("$1")
    fi
    shift
    ;;
  *)
    POSITIONAL+=("$1")
    shift
    ;;
  esac
done
set -- "${POSITIONAL[@]+"${POSITIONAL[@]}"}"

# Get server list
if [[ $# -gt 0 ]]; then
  SERVERS=("$@")
else
  # Auto-discover JSONL if not specified
  if [[ -z "$JSONL" ]]; then
    JSONL=$(find "$SCRIPT_DIR" -maxdepth 1 -name "merged-*.jsonl" -print -quit 2>/dev/null || true)
  fi
  if [[ -z "$JSONL" || ! -f "$JSONL" ]]; then
    echo "error: no JSONL file found. Provide --jsonl FILE or place a merged-*.jsonl in $SCRIPT_DIR" >&2
    exit 1
  fi
  echo "Extracting servers from $(basename "$JSONL")..." >&2
  mapfile -t SERVERS < <(python3 -c "
import json, sys
servers = set()
with open('$JSONL') as f:
    for line in f:
        try:
            e = json.loads(line)
            sender = e.get('sender', '')
            if ':' in sender:
                servers.add(sender.split(':', 1)[1])
        except: pass
for s in sorted(servers):
    print(s)
")
fi

# Filter out already-scanned servers if requested
if [[ "$SKIP_SCANNED" == true ]]; then
  before=${#SERVERS[@]}
  mapfile -t ALREADY < <(jq -r '.servers | to_entries[] | select(.value.scan_status != null) | .key' "$KNOWN" 2>/dev/null || true)
  declare -A already_set
  for s in "${ALREADY[@]}"; do already_set["$s"]=1; done
  FILTERED=()
  for s in "${SERVERS[@]}"; do
    if [[ -z "${already_set[$s]+x}" ]]; then
      FILTERED+=("$s")
    fi
  done
  SERVERS=("${FILTERED[@]+"${FILTERED[@]}"}")
  skipped=$((before - ${#SERVERS[@]}))
  echo "Skipped $skipped already-scanned servers" >&2
fi

if [[ ${#SERVERS[@]} -eq 0 ]]; then
  echo "No servers to scan."
  exit 0
fi

echo "Scanning ${#SERVERS[@]} servers (${JOBS} workers, ${TIMEOUT}s timeout)..."
echo

# Write the parser script to a temp file to avoid bash quoting hell
PARSER=$(mktemp /tmp/scan-parse-XXXXXX.py)
cat >"$PARSER" <<'PYEOF'
import json, re, sys
from datetime import datetime, timezone

outfile = sys.argv[1]
srv = sys.argv[2]
status = sys.argv[3]
resultfile = sys.argv[4]

with open(outfile) as f:
    output = f.read()

result = {
    'server': srv,
    'scan_status': status,
    'scan_date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}

# Core endpoints
fed_url = re.search(r'Federation url:\s*(\S+)', output)
fed_assume = re.search(r'Assuming federation url:\s*(\S+)', output)
client_url = re.search(r'Client url:\s*(\S+)', output)
version_match = re.search(r'Server version:\s*(.+)', output)

if fed_url:
    result['federation_url'] = fed_url.group(1)
elif fed_assume:
    result['federation_url'] = fed_assume.group(1)
if client_url:
    result['client_url'] = client_url.group(1)
if version_match:
    result['version'] = version_match.group(1).strip()

# Well-known
result['well_known_server'] = 'Server well-known' in output and not re.search(r'No server well-known', output, re.IGNORECASE)
result['well_known_client_cors'] = bool(re.search(r'Client well-known has proper CORS', output))

# API health
result['federation_api_ok'] = bool(re.search(r'Federation API endpoints seem to work', output))
result['client_api_ok'] = bool(re.search(r'Client API endpoints seem to work', output))
result['server_version_cors'] = not bool(re.search(r'Server version endpoint has no CORS', output))

# Features
result['qr_login'] = 'enabled' if 'QR code login is enabled' in output else 'disabled'
result['public_directory'] = 'enabled' if 'Public room directory is enabled' in output else 'disabled'
result['room_summaries'] = 'room summaries' in output.lower() and 'No room summaries' not in output

# Registration
if re.search(r'registration.*forbidden', output, re.IGNORECASE):
    result['registration'] = 'closed'
elif re.search(r'open registration', output, re.IGNORECASE):
    result['registration'] = 'open'
else:
    result['registration'] = 'unknown'

# MatrixRTC / SFU
result['matrixrtc_sfu'] = bool(re.search(r'MatrixRTC SFU configured', output)) and 'No MatrixRTC' not in output
result['matrixrtc_delayed_events'] = 'delayed events work' in output
livekit = re.search(r'livekit service URL:\s*(\S+)', output, re.IGNORECASE)
if livekit:
    result['livekit_url'] = livekit.group(1)
jwtauth_url = re.search(r'JWTauth healtz url:\s*(\S+)', output)
if jwtauth_url:
    result['jwtauth_url'] = jwtauth_url.group(1)
result['jwtauth_ok'] = 'JWTauth responds' in output

# Issues (all fail lines — the X mark is U+10102)
issues = []
for line in output.splitlines():
    stripped = line.strip()
    if stripped.startswith('\U00010102'):
        issues.append(stripped[2:].strip())

checks_pass = output.count('\u2714')  # checkmark
checks_fail = output.count('\U00010102')  # X mark

if issues:
    result['issues'] = issues
result['scan_checks'] = f'{checks_pass}\u2714 {checks_fail}\u2718'

with open(resultfile, 'w') as f:
    json.dump(result, f)

tag = '\u2714' if status == 'ok' else '\u2718'
ver = result.get('version', '')[:40]
print(f'{tag} {srv}: {status} ({checks_pass}\u2714 {checks_fail}\u2718) {ver}')
PYEOF

# Worker function: scan a single server
scan_one() {
  local srv="$1"
  local outfile="$RESULTS_DIR/${srv}.txt"
  local resultfile="$RESULTS_DIR/${srv}.result.json"

  # Run testmatrix (verbose — no -q) with timeout
  local status
  if timeout "$TIMEOUT" "$TESTMATRIX" "$srv" >"$outfile" 2>&1; then
    status="ok"
  else
    local rc=$?
    if [[ $rc -eq 124 ]]; then
      status="timeout"
    else
      status="error"
    fi
  fi

  python3 "$PARSER" "$outfile" "$srv" "$status" "$resultfile" 2>/dev/null ||
    echo "✘ $srv: parse error"
}

export -f scan_one
export TESTMATRIX TIMEOUT RESULTS_DIR PARSER

# Run in parallel using background job pool
active=0
for srv in "${SERVERS[@]}"; do
  scan_one "$srv" &
  active=$((active + 1))
  if [[ $active -ge $JOBS ]]; then
    wait -n 2>/dev/null || true
    active=$((active - 1))
  fi
done
wait

rm -f "$PARSER"

echo
echo "Merging results into $KNOWN..."

# Merge all result fragments into known-servers.json (single-threaded, safe)
python3 -c "
import json, glob, os

known_file = '$KNOWN'
results_dir = '$RESULTS_DIR'

with open(known_file) as f:
    known = json.load(f)

# Fields to copy from scan results
SCAN_FIELDS = [
    'federation_url', 'client_url', 'version',
    'well_known_server', 'well_known_client_cors',
    'federation_api_ok', 'client_api_ok', 'server_version_cors',
    'qr_login', 'public_directory', 'room_summaries', 'registration',
    'matrixrtc_sfu', 'matrixrtc_delayed_events', 'livekit_url', 'jwtauth_url', 'jwtauth_ok',
    'issues', 'scan_status', 'scan_checks', 'scan_date',
]

merged = 0
for rf in sorted(glob.glob(os.path.join(results_dir, '*.result.json'))):
    try:
        with open(rf) as f:
            r = json.loads(f.read())
        srv = r.pop('server')
        entry = known['servers'].get(srv, {})

        for k in SCAN_FIELDS:
            if k in r:
                entry[k] = r[k]
            elif k == 'issues' and k in entry:
                del entry[k]

        # Auto-detect software from version string
        ver = r.get('version', '').lower()
        if ver:
            if 'synapse' in ver:
                entry.setdefault('software', 'synapse')
            elif 'continuwuity' in ver or 'conduwuit' in ver or 'conduit' in ver:
                entry.setdefault('software', 'continuwuity')
            elif 'dendrite' in ver:
                entry.setdefault('software', 'dendrite')

        known['servers'][srv] = entry
        merged += 1
    except Exception as e:
        print(f'  warning: failed to merge {rf}: {e}')

from datetime import date
known['\$updated'] = str(date.today())

with open(known_file, 'w') as f:
    json.dump(known, f, indent=2, ensure_ascii=False)
    f.write('\n')

print(f'Merged {merged} results into {known_file}')
"

echo "Done."
