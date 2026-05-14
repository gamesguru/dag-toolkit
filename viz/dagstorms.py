#!/usr/bin/env python3
"""Auto-detect fork storms in a JSONL DAG and render graphs for each.

Usage:
    dagstorms.py <file.jsonl> [options]
    dagstorms.py --dir /path/to/dags --room <slug> [options]

Examples:
    dagstorms.py merged.jsonl --csv                    # CSV of all storms
    dagstorms.py merged.jsonl --csv --sort span        # Longest sustained storms first
    dagstorms.py merged.jsonl --top 5 --outdir storms/ # Render top 5
    dagstorms.py --dir /dags --room <slug> --threshold 3.0
"""

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path


def load_profile(path: str) -> dict[int, tuple[int, int]]:
    """Single-pass: build depth -> (event_count, total_prev_events)."""
    profile: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                d = ev.get("depth", 0)
                prev = len(ev.get("prev_events", []))
                profile[d][0] += 1
                profile[d][1] += prev
            except json.JSONDecodeError:
                continue
    return {d: (b[0], b[1]) for d, b in profile.items()}


def find_storms(
    profile: dict[int, tuple[int, int]], threshold: float, context: int
) -> list[dict]:
    """Find storm clusters: contiguous depth ranges where BF > threshold.

    Returns list of storm dicts with full statistics.
    """
    storm_depths = []
    for d, (ev, prev) in sorted(profile.items()):
        if ev > 0 and prev / ev >= threshold:
            storm_depths.append((d, ev, prev, prev / ev))

    if not storm_depths:
        return []

    # Cluster nearby storm depths (gap <= 3 depths apart)
    clusters: list[list[tuple]] = []
    current: list[tuple] = [storm_depths[0]]

    for entry in storm_depths[1:]:
        if entry[0] - current[-1][0] <= 3:
            current.append(entry)
        else:
            clusters.append(current)
            current = [entry]
    clusters.append(current)

    results = []
    for cluster in clusters:
        depths = [e[0] for e in cluster]
        bfs = [e[3] for e in cluster]
        evts = [e[1] for e in cluster]
        prevs = [e[2] for e in cluster]
        peak_idx = bfs.index(max(bfs))
        total_ev = sum(evts)
        total_prev = sum(prevs)
        span = max(depths) - min(depths) + 1

        lo = min(depths) - context
        hi = max(depths) + context

        results.append(
            {
                "lo": lo,
                "hi": hi,
                "depth_lo": min(depths),
                "depth_hi": max(depths),
                "span": span,
                "peak_depth": cluster[peak_idx][0],
                "peak_bf": cluster[peak_idx][3],
                "peak_prev": cluster[peak_idx][2],
                "avg_bf": total_prev / total_ev if total_ev else 0,
                "storm_depths": len(cluster),
                "total_events": total_ev,
                "total_prev": total_prev,
            }
        )

    return results


def render_storm(
    jsonl_path: str, storm: dict, outdir: str, index: int, script_dir: str
) -> str:
    """Render a single storm to PNG, returns output path."""
    lo, hi = storm["lo"], storm["hi"]
    peak = storm["peak_depth"]
    bf = storm["peak_bf"]

    name = f"storm_{index:02d}_d{peak}_bf{bf:.0f}"
    dot_path = os.path.join(outdir, f"{name}.dot")
    png_path = os.path.join(outdir, f"{name}.png")

    title = (
        f"Storm #{index} @ d{storm['depth_lo']}..{storm['depth_hi']} "
        f"(peak BF={bf:.1f}, span={storm['span']}, "
        f"{storm['storm_depths']} hot depths, {storm['total_events']} events)"
    )

    graph_script = os.path.join(script_dir, "daggraph.py")
    subprocess.run(
        [
            sys.executable,
            graph_script,
            jsonl_path,
            "--depth",
            f"{lo}:{hi}",
            "--title",
            title,
            "--output",
            dot_path,
        ],
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["dot", "-Tpng", dot_path, "-o", png_path],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"  dot failed for {name}: {result.stderr.decode()}", file=sys.stderr)
        return ""

    return png_path


def resolve_input(args) -> str:
    """Resolve input JSONL file from args."""
    if args.file:
        return args.file

    if args.dir and args.room:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        merge_script = os.path.join(script_dir, "dagmerge.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()

        pattern = str(Path(args.dir) / f"{args.prefix}-{args.room}-*.jsonl")
        files = sorted(glob.glob(pattern))
        if not files:
            print(f"No files matching {pattern}", file=sys.stderr)
            sys.exit(1)

        print(f"Merging {len(files)} files...", file=sys.stderr)
        subprocess.run(
            [sys.executable, merge_script, *files, "-o", tmp.name],
            check=True,
        )
        return tmp.name

    print("Error: provide a JSONL file or --dir + --room", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Auto-detect and visualize fork storms"
    )
    parser.add_argument("file", nargs="?", help="Merged JSONL file")
    parser.add_argument("-d", "--dir", help="Directory for globbing")
    parser.add_argument("--room", help="Room slug for globbing")
    parser.add_argument("--prefix", default="remote-dag")
    parser.add_argument(
        "--threshold",
        type=float,
        default=2.0,
        help="BF threshold for storm detection (default: 2.0)",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=5,
        help="Depth context around storm peaks (default: 5)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max storms to render (default: 10)",
    )
    parser.add_argument(
        "--outdir",
        "-o",
        default="storms",
        help="Output directory for PNGs (default: storms/)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Output all storm clusters as CSV (no rendering)",
    )
    parser.add_argument(
        "--sort",
        choices=["bf", "span", "events", "depth"],
        default="span",
        help="Sort order for CSV/table output (default: span)",
    )
    args = parser.parse_args()

    jsonl_path = resolve_input(args)

    # Profile
    print("Profiling depths...", file=sys.stderr)
    profile = load_profile(jsonl_path)
    print(f"  {len(profile)} distinct depths", file=sys.stderr)

    # Detect storms
    storms = find_storms(profile, args.threshold, args.context)
    print(
        f"  {len(storms)} storm clusters (BF > {args.threshold})",
        file=sys.stderr,
    )

    if not storms:
        print("No storms detected.", file=sys.stderr)
        return

    # Sort
    sort_keys = {
        "bf": lambda s: -s["peak_bf"],
        "span": lambda s: (-s["storm_depths"], -s["peak_bf"]),
        "events": lambda s: -s["total_events"],
        "depth": lambda s: s["depth_lo"],
    }
    storms.sort(key=sort_keys[args.sort])

    # CSV mode: dump all clusters
    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(
            [
                "depth_lo",
                "depth_hi",
                "span",
                "storm_depths",
                "total_events",
                "total_prev",
                "avg_bf",
                "peak_depth",
                "peak_bf",
                "peak_prev",
            ]
        )
        for s in storms:
            writer.writerow(
                [
                    s["depth_lo"],
                    s["depth_hi"],
                    s["span"],
                    s["storm_depths"],
                    s["total_events"],
                    s["total_prev"],
                    f"{s['avg_bf']:.2f}",
                    s["peak_depth"],
                    f"{s['peak_bf']:.2f}",
                    s["peak_prev"],
                ]
            )
        return

    # Render top N
    os.makedirs(args.outdir, exist_ok=True)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    to_render = storms[: args.top]

    print(f"\nRendering top {len(to_render)} storms → {args.outdir}/", file=sys.stderr)
    print(
        f"{'#':<4} {'DEPTH RANGE':<20} {'SPAN':<6} {'HOT':<5} "
        f"{'EVENTS':<8} {'PEAK BF':<9} {'AVG BF':<8} {'FILE'}",
        file=sys.stderr,
    )
    print("-" * 90, file=sys.stderr)

    for i, storm in enumerate(to_render, 1):
        png = render_storm(jsonl_path, storm, args.outdir, i, script_dir)
        status = os.path.basename(png) if png else "FAILED"
        depth_range = f"d{storm['depth_lo']}..{storm['depth_hi']}"
        print(
            f"{i:<4} {depth_range:<20} {storm['span']:<6} "
            f"{storm['storm_depths']:<5} {storm['total_events']:<8} "
            f"{storm['peak_bf']:<9.1f} {storm['avg_bf']:<8.2f} {status}",
            file=sys.stderr,
        )

    print(f"\n✓ {len(to_render)} storm graphs in {args.outdir}/", file=sys.stderr)


if __name__ == "__main__":
    main()
