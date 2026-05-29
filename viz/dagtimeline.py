#!/usr/bin/env python3
"""Generate a technical text timeline from a Matrix DAG JSONL.

Usage:
    dagtimeline.py <file.jsonl>
"""

import sys
import json
from datetime import datetime, timezone

def short_id(eid):
    return eid[:12] if len(eid) > 12 else eid

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file.jsonl>")
        sys.exit(1)

    path = sys.argv[1]
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Sort by depth and then by timestamp
    events.sort(key=lambda e: (e.get("depth", 0), e.get("origin_server_ts", 0)))

    print(f"{'DEPTH':>6} {'TIMESTAMP':<20} {'SENDER':<25} {'TYPE':<20} {'CONTENT/INFO':<40} {'EVENT_ID'}")
    print("-" * 130)

    for ev in events:
        depth = ev.get("depth", 0)
        ts = ev.get("origin_server_ts", 0)
        dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        sender = ev.get("sender", "")
        etype = ev.get("type", "").replace("m.room.", "")
        eid = ev.get("event_id", "")
        
        info = ""
        content = ev.get("content", {})
        if ev.get("type") == "m.room.member":
            membership = content.get("membership", "?")
            sk = ev.get("state_key", "")
            info = f"{membership}: {sk}"
        elif ev.get("type") == "m.room.message":
            body = content.get("body", "").replace("\n", " ")
            if len(body) > 37:
                body = body[:37] + "..."
            info = body
        elif ev.get("type") == "m.reaction":
            rel = content.get("m.relates_to", {})
            key = rel.get("key", "?")
            info = f"reaction: {key}"
        elif ev.get("type") == "m.room.power_levels":
            info = "power_levels update"
        elif ev.get("type") == "m.room.redaction":
            redacts = ev.get("redacts", "")
            info = f"redacts: {short_id(redacts)}"
        
        print(f"{depth:6} {ts_str:<20} {sender:<25} {etype:<20} {info:<40} {eid}")

if __name__ == "__main__":
    main()
