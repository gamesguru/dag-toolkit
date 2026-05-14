# dag-toolkit

Federated DAG comparison, profiling, and fork-storm analysis.

## Build

```bash
# Dependencies: simdjson, graphviz, python3
# Arch: pacman -S simdjson graphviz python
# Debian: apt install libsimdjson-dev graphviz python3

# Build the C++ engine
make dagcmp

# Run tests + lint
make test/all
```

## Quick Start

```bash
# 1. Compare server DAGs for a room
./build/dagcmp <room-slug> -d /path/to/dags/

# 2. Direct file input
./build/dagcmp -i server1.jsonl -i server2.jsonl

# 3. Depth profiling (CSV)
./build/dagcmp <room-slug> -d /path/to/dags/ --profile out.csv

# 4. Merge all server DAGs into one deduplicated file
make merge ROOM=<slug> DIR=/path/to/dags/

# 5. Auto-detect and render fork storms
make stormviz FILE=merged.jsonl

# 6. CSV sweep of all storm clusters (sort by sustained span)
python3 viz/dagstorms.py merged.jsonl --csv --sort span

# 7. Render a specific depth range
make graph FILE=merged.jsonl DEPTH=22806:22844
```

## CLI Reference

```
dagcmp <room-slug> [options]
dagcmp -i <file1> [-i <file2> ...] [options]

Options:
  -i, --input FILE  Input JSONL file (repeatable)
  -d, --dir DIR     Working directory for JSONL files (default: .)
  --prefix PREFIX   JSONL file prefix (default: remote-dag)
  -v, --verbose     Show per-user membership diffs
  -r, --rank        Rank servers by F1 score
  -c, --chain       Greedy chain coverage analysis
  --profile [FILE]  Emit per-depth BF profile as CSV
  --version VER     State-res version (default: v2-1)
  -h, --help        Show help
```

## Make Targets

| Target     | Description                                                     |
| ---------- | --------------------------------------------------------------- |
| `dagcmp`   | Build the C++ comparison engine                                 |
| `test/all` | Build, run tests, lint                                          |
| `merge`    | Merge JSONL files (`ROOM=` `DIR=`)                              |
| `graph`    | Render DAG graph (`FILE=` `DEPTH=`)                             |
| `stormviz` | Auto-detect and render storm graphs (`FILE=` or `ROOM=` `DIR=`) |
| `storms`   | Visualize BF profile from CSV (`CSV=`)                          |
| `profile`  | Generate BF depth profile (`ROOM=`)                             |

## Visualization Scripts

| Script             | Description                                      |
| ------------------ | ------------------------------------------------ |
| `viz/dagmerge.py`  | Merge + deduplicate JSONL files by event_id      |
| `viz/daggraph.py`  | Render JSONL DAG fragment as Graphviz DOT/PNG    |
| `viz/dagstorms.py` | Auto-detect fork storms, CSV sweep, render top-N |
| `viz/dagviz.py`    | BF profile matplotlib plots + heatmaps           |

## Investigating a Specific Event

Once you have a merged JSONL and know what you're looking for, here's the
workflow to generate a visual:

```bash
# Step 1: Merge all server DAGs into one file
make merge ROOM=c10y-fNiMx5ijtgGFibzPUfNs9hpQvnJYPTV-fD2KPk-v12 \
     DIR=/path/to/dags/

# Step 2: Find the event's depth (grep for user, event_id, etc.)
python3 -c "
import json
from datetime import datetime, timezone
with open('merged.jsonl') as f:
    for line in f:
        ev = json.loads(line.strip())
        if 'ggdev' in ev.get('state_key', ''):
            ts = ev.get('origin_server_ts', 0)
            dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
            ms = ev.get('content', {}).get('membership', '?')
            print(f'd={ev[\"depth\"]} {dt:%Y-%m-%d %H:%M:%S} {ms} {ev[\"event_id\"][:20]}')
"
# Output: d=48390 2026-04-15 07:04:02 join $_5wjEmfHri5NJY49yDP
#         d=48391 2026-04-15 07:05:38 leave $jZcqs5f5sDAcCtnZHtW

# Step 3: Render the DAG around that depth (±5-10 for context)
make graph FILE=merged.jsonl DEPTH=48385:48400
# → /tmp/dag.png

# Step 4: Open it
xdg-open /tmp/dag.png
```

### Finding Fork Storms

```bash
# Full CSV sweep — all storm clusters sorted by sustained depth span
python3 viz/dagstorms.py merged.jsonl --csv --sort span > storms.csv

# Sort by peak branching factor instead
python3 viz/dagstorms.py merged.jsonl --csv --sort bf > storms.csv

# Auto-render the top 5 longest sustained storms as PNGs
make stormviz FILE=merged.jsonl TOP=5 THRESHOLD=2.0
# → storms/storm_01_d22840_bf8.png, storms/storm_02_d54581_bf3.png, ...
```

### Checking Per-Server Coverage

Not all servers have complete DAGs. To check which servers saw a specific event:

```bash
python3 -c "
import json, glob
for f in sorted(glob.glob('/path/to/dags/remote-dag-SLUG-*.jsonl')):
    server = f.split('-v12-')[-1].replace('.jsonl','')
    found = False
    for line in open(f):
        if 'EVENT_ID_PREFIX' in line:
            found = True; break
    print(f'{server:<30} {\"✓\" if found else \"✗\"} ')
"
```

### Converting DOT to PNG

The `make graph` and `make stormviz` targets render PNGs automatically.
To manually convert a DOT file:

```bash
# PNG (raster)
dot -Tpng storms/storm_01_d22840_bf8.dot -o storm.png

# SVG (vector, better for large graphs)
dot -Tsvg storms/storm_01_d22840_bf8.dot -o storm.svg

# PDF
dot -Tpdf storms/storm_01_d22840_bf8.dot -o storm.pdf
```

## Architecture

```
dagcmp (C++20 + simdjson)
├── src/main.cpp          CLI entry point
├── src/analyzer.cpp      Parallel per-domain analysis (std::async)
├── src/jsonl_reader.cpp  simdjson JSONL streaming parser
├── src/ruma_runner.cpp   ruma-lean subprocess orchestration
├── src/display.cpp       Dynamic-width tabular output
└── test/test_jsonl.cpp   CTest suite

viz/ (Python)
├── dagmerge.py           JSONL merge/dedup
├── daggraph.py           DOT graph renderer
├── dagstorms.py          Storm detection + auto-viz
└── dagviz.py             BF profile plots
```
