#!/usr/bin/env python3
"""Merge multiple JSONL DAG files, deduplicating by event_id.

Usage:
    dagmerge.py <file1.jsonl> [file2.jsonl ...] [-o merged.jsonl]
    dagmerge.py --dir /path/to/dags --prefix remote-dag --room <slug> [-o merged.jsonl]
"""

import argparse
import glob
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Merge JSONL DAGs, deduplicate by event_id"
    )
    parser.add_argument("files", nargs="*", help="JSONL files to merge")
    parser.add_argument("-d", "--dir", help="Directory to glob for files")
    parser.add_argument(
        "--prefix", default="remote-dag", help="File prefix for globbing"
    )
    parser.add_argument("--room", help="Room slug for globbing")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument(
        "--sort",
        choices=["depth", "ts", "none"],
        default="depth",
        help="Sort order (default: depth)",
    )
    args = parser.parse_args()

    # Collect input files
    files = list(args.files)
    if args.dir and args.room:
        pattern = str(Path(args.dir) / f"{args.prefix}-{args.room}-*.jsonl")
        files.extend(sorted(glob.glob(pattern)))

    if not files:
        print("No input files", file=sys.stderr)
        sys.exit(1)

    # Deduplicate
    seen = {}  # event_id -> line
    for path in files:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    eid = ev.get("event_id")
                    if eid and eid not in seen:
                        seen[eid] = (ev, line)
                except json.JSONDecodeError:
                    continue

    events = list(seen.values())

    # Sort
    if args.sort == "depth":
        events.sort(
            key=lambda x: (x[0].get("depth", 0), x[0].get("origin_server_ts", 0))
        )
    elif args.sort == "ts":
        events.sort(key=lambda x: x[0].get("origin_server_ts", 0))

    print(f"Merged {len(files)} files → {len(events)} unique events", file=sys.stderr)

    out = open(args.output, "w") if args.output else sys.stdout
    try:
        for _, line in events:
            out.write(line + "\n")
    finally:
        if args.output:
            out.close()
            print(f"Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
