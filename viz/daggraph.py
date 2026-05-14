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
from datetime import datetime, timezone


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

    # Timestamp
    ts = ev.get("origin_server_ts", 0)
    if ts:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        ts_str = dt.strftime("%-d %b %Y, %H:%M")
    else:
        ts_str = ""

    parts = [f"d={depth}", short_type]
    if etype == "m.room.member":
        membership = ev.get("content", {}).get("membership", "?")
        prev_membership = (
            ev.get("unsigned", {}).get("prev_content", {}).get("membership")
        )
        sk = ev.get("state_key", "")

        # Same membership as before → profile update (rename/avatar)
        if prev_membership == membership and prev_membership is not None:
            displayname = ev.get("content", {}).get("displayname", sk)
            # Escape for DOT label
            displayname = displayname.replace('"', "'").replace("\\", "")
            parts.append(f"rename:{displayname}")
        else:
            parts.append(f"{membership}:{sk}")
    elif sender:
        parts.append(sender)

    label = f"{eid}\\n{' '.join(parts)}"
    if ts_str:
        label += f"\\n{ts_str}"
    return label


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


def render_dot(
    events: list[dict],
    title: str = "DAG",
    all_idx: dict | None = None,
    primary_ids: set | None = None,
) -> str:
    lines = [
        f'digraph "{title}" {{',
        "  rankdir=TB;",
        f'  label="{title}";',
        "  labelloc=t;",
        "  labeljust=c;",
        "  fontsize=14;",
        '  fontname="monospace bold";',
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
        is_followed = primary_ids is not None and eid not in primary_ids
        style = '"dashed,filled,rounded"' if is_followed else '"filled,rounded"'
        fill = "#f5f5f5" if is_followed else color
        node_id = (
            eid.replace("$", "e_").replace("-", "_").replace("+", "_").replace("/", "_")
        )
        lines.append(
            f'  "{node_id}" [label="{label}", style={style}, fillcolor="{fill}", penwidth={penwidth}];'
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
                if all_idx and prev in all_idx:
                    ext = all_idx[prev]
                    ext_label = event_label(ext)
                else:
                    ext_label = f"{short_id(prev)}\\n(external)"
                lines.append(
                    f'  "{src}" [label="{ext_label}", '
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


def follow_externals(
    events: list[dict],
    all_events_idx: dict[str, dict],
    max_hops: int,
    max_nodes: int = 0,
) -> list[dict]:
    """Recursively resolve external prev_events up to max_hops levels."""
    included = {ev["event_id"] for ev in events}
    frontier = set()

    # Seed frontier with external refs from current events
    for ev in events:
        for prev in ev.get("prev_events", []):
            if prev not in included:
                frontier.add(prev)

    added = []
    for _hop in range(max_hops):
        if not frontier:
            break
        if max_nodes > 0 and len(added) >= max_nodes:
            break
        next_frontier: set[str] = set()
        for eid in frontier:
            if max_nodes > 0 and len(added) >= max_nodes:
                break
            if eid in all_events_idx and eid not in included:
                ev = all_events_idx[eid]
                added.append(ev)
                included.add(eid)
                for prev in ev.get("prev_events", []):
                    if prev not in included:
                        next_frontier.add(prev)
        frontier = next_frontier

    return events + added


def main():
    parser = argparse.ArgumentParser(description="Render Matrix DAG as graphviz DOT")
    parser.add_argument("jsonl", help="JSONL file with Matrix events")
    parser.add_argument(
        "--depth",
        help="Depth range filter, e.g. 7850:7860",
    )
    parser.add_argument(
        "--follow",
        "-f",
        type=int,
        default=0,
        metavar="N",
        help="Follow external prev_events up to N hops beyond depth window",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=0,
        metavar="N",
        help="Cap total followed nodes (0 = unlimited)",
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

    all_events = load_events(args.jsonl)
    if not all_events:
        print("No events found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(all_events)} events", file=sys.stderr)

    # Build full index for --follow lookups
    all_idx = {ev["event_id"]: ev for ev in all_events if "event_id" in ev}

    events = all_events

    # Filter by depth range
    if args.depth:
        parts = args.depth.split(":")
        lo = int(parts[0]) if parts[0] else 0
        hi = int(parts[1]) if len(parts) > 1 and parts[1] else float("inf")
        events = [e for e in events if lo <= e.get("depth", 0) <= hi]
        print(f"Filtered to {len(events)} events in depth {lo}..{hi}", file=sys.stderr)

    # Follow external refs
    primary_ids = {ev["event_id"] for ev in events}
    if args.follow > 0 and args.depth:
        before = len(events)
        events = follow_externals(events, all_idx, args.follow, args.max_nodes)
        extra = len(events) - before
        if extra:
            print(
                f"Followed {extra} external events ({args.follow} hops)",
                file=sys.stderr,
            )

    # Build descriptive title
    import os

    basename = os.path.splitext(os.path.basename(args.jsonl))[0]
    # Strip "merged-" prefix for cleaner display
    room_label = (
        basename.replace("merged-", "", 1)
        if basename.startswith("merged-")
        else basename
    )

    # Compute date and depth range from primary events
    primary = [
        ev for ev in events if primary_ids is None or ev.get("event_id") in primary_ids
    ]
    if primary:
        timestamps = [
            ev.get("origin_server_ts", 0)
            for ev in primary
            if ev.get("origin_server_ts")
        ]
        depths = [ev.get("depth", 0) for ev in primary]
        depth_str = f"depth {min(depths)}..{max(depths)}"
        if timestamps:
            dt_min = datetime.fromtimestamp(min(timestamps) / 1000, tz=timezone.utc)
            dt_max = datetime.fromtimestamp(max(timestamps) / 1000, tz=timezone.utc)
            if dt_min.date() == dt_max.date():
                date_str = dt_min.strftime("%-d %b %Y")
            else:
                date_str = (
                    f"{dt_min.strftime('%-d %b')} – {dt_max.strftime('%-d %b %Y')}"
                )
        else:
            date_str = ""
    else:
        depth_str = ""
        date_str = ""

    title_lines = [f"DAG – {room_label}"]
    if date_str:
        title_lines.append(date_str)
    if depth_str:
        title_lines.append(depth_str)
    auto_title = "\\n".join(title_lines)

    dot = render_dot(
        events,
        title=auto_title,
        all_idx=all_idx,
        primary_ids=primary_ids,
    )

    if args.output:
        with open(args.output, "w") as f:
            f.write(dot)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(dot)


if __name__ == "__main__":
    main()
