#!/usr/bin/env python3
"""Render a Matrix DAG from JSONL as a graphviz DOT graph.

Usage:
    daggraph.py <file.jsonl> [options]

Examples:
    daggraph.py events.jsonl --output dag.dot           # Full DOT
    daggraph.py events.jsonl --depth 7850:7860 -o storm.dot  # Depth range
    daggraph.py events.jsonl --depth 7850:7860 | dot -Tpng -o storm.png
"""

import argparse
import json
import sys
from collections import defaultdict


def load_events(path: str) -> list[dict]:
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
    return events


def short_id(event_id: str) -> str:
    """Shorten $abcdefghij... to $abcdef for readability."""
    if len(event_id) > 8:
        return event_id[:8]
    return event_id


def event_label(ev: dict) -> str:
    """Build a concise label for the node."""
    eid = short_id(ev.get("event_id", "?"))
    depth = ev.get("depth", "?")
    etype = ev.get("type", "")

    # Shorten type
    short_type = etype.replace("m.room.", "")

    sender = ev.get("sender", "")
    # Extract localpart
    if sender.startswith("@"):
        sender = sender.split(":")[0][1:]

    parts = [f"d{depth}", short_type]
    if etype == "m.room.member":
        membership = ev.get("content", {}).get("membership", "?")
        sk = ev.get("state_key", "")
        if sk.startswith("@"):
            sk = sk.split(":")[0][1:]
        parts.append(f"{membership}:{sk}")
    elif sender:
        parts.append(sender)

    return f"{eid}\\n{' '.join(parts)}"


def event_color(ev: dict) -> str:
    etype = ev.get("type", "")
    if etype == "m.room.create":
        return "#2ecc71"
    if etype == "m.room.member":
        membership = ev.get("content", {}).get("membership", "")
        if membership == "join":
            return "#3498db"
        if membership == "leave":
            return "#e67e22"
        if membership == "ban":
            return "#e74c3c"
        return "#9b59b6"
    if etype == "m.room.message":
        return "#ecf0f1"
    return "#bdc3c7"


def render_dot(events: list[dict], title: str = "DAG") -> str:
    lines = [
        f'digraph "{title}" {{',
        "  rankdir=TB;",
        '  node [shape=box, style="filled,rounded", fontsize=9, fontname="monospace"];',
        '  edge [color="#7f8c8d"];',
        "",
    ]

    # Index events by ID
    by_id = {ev["event_id"]: ev for ev in events if "event_id" in ev}
    id_set = set(by_id.keys())

    # Group by depth for rank constraints
    by_depth: dict[int, list[str]] = defaultdict(list)
    for ev in events:
        by_depth[ev.get("depth", 0)].append(ev["event_id"])

    # Nodes
    for ev in events:
        eid = ev["event_id"]
        label = event_label(ev)
        color = event_color(ev)
        n_prev = len(ev.get("prev_events", []))
        # Thicker border for high-fanin events
        penwidth = "2.0" if n_prev > 2 else "1.0"
        node_id = (
            eid.replace("$", "e_").replace("-", "_").replace("+", "_").replace("/", "_")
        )
        lines.append(
            f'  "{node_id}" [label="{label}", fillcolor="{color}", penwidth={penwidth}];'
        )

    lines.append("")

    # Edges (prev_events → this event)
    for ev in events:
        eid = ev["event_id"]
        dst = (
            eid.replace("$", "e_").replace("-", "_").replace("+", "_").replace("/", "_")
        )
        for prev in ev.get("prev_events", []):
            src = (
                prev.replace("$", "e_")
                .replace("-", "_")
                .replace("+", "_")
                .replace("/", "_")
            )
            if prev in id_set:
                lines.append(f'  "{src}" -> "{dst}";')
            else:
                # External reference — dashed edge to phantom node
                lines.append(
                    f'  "{src}" [label="{short_id(prev)}\\n(external)", '
                    f'style="dashed,rounded", fillcolor="#f5f5f5"];'
                )
                lines.append(f'  "{src}" -> "{dst}" [style=dashed];')

    # Depth rank constraints
    for depth in sorted(by_depth.keys()):
        eids = by_depth[depth]
        if len(eids) > 1:
            node_ids = [
                '"'
                + eid.replace("$", "e_")
                .replace("-", "_")
                .replace("+", "_")
                .replace("/", "_")
                + '"'
                for eid in eids
            ]
            lines.append(f"  {{ rank=same; {'; '.join(node_ids)} }}")

    lines.append("}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Render Matrix DAG as graphviz DOT")
    parser.add_argument("jsonl", help="JSONL file with Matrix events")
    parser.add_argument(
        "--depth",
        help="Depth range filter, e.g. 7850:7860",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--title",
        "-t",
        default="DAG",
        help="Graph title",
    )
    args = parser.parse_args()

    events = load_events(args.jsonl)
    if not events:
        print("No events found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(events)} events", file=sys.stderr)

    # Filter by depth range
    if args.depth:
        parts = args.depth.split(":")
        lo = int(parts[0]) if parts[0] else 0
        hi = int(parts[1]) if len(parts) > 1 and parts[1] else float("inf")
        events = [e for e in events if lo <= e.get("depth", 0) <= hi]
        print(f"Filtered to {len(events)} events in depth {lo}..{hi}", file=sys.stderr)

    dot = render_dot(events, title=args.title)

    if args.output:
        with open(args.output, "w") as f:
            f.write(dot)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(dot)


if __name__ == "__main__":
    main()
