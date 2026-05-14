#!/usr/bin/env python3
"""Visualize per-depth branching factor profiles from dagcmp --profile output.

Usage:
    dagviz.py <profile.csv> [options]

Examples:
    dagviz.py bf_profile.csv                       # Interactive plot
    dagviz.py bf_profile.csv --output storms.png   # Save to file
    dagviz.py bf_profile.csv --heatmap --window 50 # Sliding-window heatmap
    dagviz.py bf_profile.csv --threshold 3.0       # Custom storm threshold
"""

import argparse
import csv
import sys
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np


@dataclass
class DepthRow:
    depth: int
    events: int
    prev_events: int
    bf: float


def load_profile(path: str) -> list[DepthRow]:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                DepthRow(
                    depth=int(row["depth"]),
                    events=int(row["events"]),
                    prev_events=int(row["prev_events"]),
                    bf=float(row["bf"]),
                )
            )
    return rows


def find_storms(rows: list[DepthRow], threshold: float) -> list[dict]:
    """Find contiguous storm regions where BF > threshold."""
    storms = []
    current = None

    for r in rows:
        if r.bf > threshold:
            if current is None:
                current = {
                    "start": r.depth,
                    "end": r.depth,
                    "peak_bf": r.bf,
                    "peak_depth": r.depth,
                    "total_events": r.events,
                    "depths": 1,
                }
            else:
                current["end"] = r.depth
                current["total_events"] += r.events
                current["depths"] += 1
                if r.bf > current["peak_bf"]:
                    current["peak_bf"] = r.bf
                    current["peak_depth"] = r.depth
        else:
            if current is not None:
                storms.append(current)
                current = None

    if current is not None:
        storms.append(current)

    return storms


def plot_bf_over_depth(
    rows: list[DepthRow],
    threshold: float,
    output: str | None,
    title: str = "Branching Factor over Depth",
):
    depths = np.array([r.depth for r in rows])
    bfs = np.array([r.bf for r in rows])

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 8), height_ratios=[3, 1], sharex=True
    )
    fig.suptitle(title, fontsize=14, fontweight="bold")

    # Top: BF line plot
    ax1.plot(depths, bfs, linewidth=0.4, color="#4a90d9", alpha=0.8, label="BF")

    # Highlight storms
    storm_mask = bfs > threshold
    if storm_mask.any():
        ax1.fill_between(
            depths,
            0,
            bfs,
            where=storm_mask,
            alpha=0.35,
            color="#e74c3c",
            label=f"Storm (BF > {threshold})",
        )

    ax1.axhline(
        y=threshold,
        color="#e74c3c",
        linestyle="--",
        linewidth=0.8,
        alpha=0.7,
        label=f"Threshold ({threshold})",
    )
    ax1.axhline(
        y=1.0,
        color="#2ecc71",
        linestyle=":",
        linewidth=0.6,
        alpha=0.5,
        label="Linear (1.0)",
    )

    ax1.set_ylabel("Branching Factor")
    ax1.set_ylim(bottom=0, top=min(bfs.max() * 1.1, bfs.max() + 2))
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.2)

    # Bottom: event density
    events = np.array([r.events for r in rows])
    ax2.fill_between(depths, 0, events, alpha=0.5, color="#9b59b6")
    ax2.set_ylabel("Events at depth")
    ax2.set_xlabel("Depth")
    ax2.grid(True, alpha=0.2)

    plt.tight_layout()

    if output:
        plt.savefig(output, dpi=150, bbox_inches="tight")
        print(f"Saved to {output}", file=sys.stderr)
    else:
        plt.show()


def plot_heatmap(
    rows: list[DepthRow],
    window: int,
    output: str | None,
    title: str = "Sliding-Window BF Heatmap",
):
    depths = np.array([r.depth for r in rows])
    bfs = np.array([r.bf for r in rows])

    # Sliding window average
    kernel = np.ones(window) / window
    smoothed = np.convolve(bfs, kernel, mode="same")

    fig, ax = plt.subplots(figsize=(16, 3))
    fig.suptitle(f"{title} (window={window})", fontsize=12, fontweight="bold")

    # Reshape into a 2D strip for imshow
    extent = [depths[0], depths[-1], 0, 1]
    ax.imshow(
        smoothed.reshape(1, -1),
        aspect="auto",
        cmap="hot_r",
        extent=extent,
        interpolation="bilinear",
    )
    ax.set_xlabel("Depth")
    ax.set_yticks([])

    cbar = plt.colorbar(ax.images[0], ax=ax, orientation="vertical", pad=0.01)
    cbar.set_label("Avg BF")

    plt.tight_layout()

    if output:
        base = output.rsplit(".", 1)
        heatmap_path = (
            f"{base[0]}_heatmap.{base[1]}" if len(base) > 1 else f"{output}_heatmap"
        )
        plt.savefig(heatmap_path, dpi=150, bbox_inches="tight")
        print(f"Saved heatmap to {heatmap_path}", file=sys.stderr)
    else:
        plt.show()


def print_storm_table(storms: list[dict], threshold: float):
    if not storms:
        print(f"\nNo storm regions found above BF threshold {threshold}")
        return

    # Sort by peak BF descending
    storms = sorted(storms, key=lambda s: s["peak_bf"], reverse=True)

    print(f"\nFork Storm Summary (BF > {threshold}):")
    print(
        f"{'PEAK_BF':>8} {'PEAK_DEPTH':>11} {'START':>8} {'END':>8} "
        f"{'SPAN':>6} {'EVENTS':>7}"
    )
    print("-" * 56)

    for s in storms[:20]:  # Top 20
        span = s["end"] - s["start"] + 1
        print(
            f"{s['peak_bf']:>8.3f} {s['peak_depth']:>11} "
            f"{s['start']:>8} {s['end']:>8} "
            f"{span:>6} {s['total_events']:>7}"
        )

    if len(storms) > 20:
        print(f"  ... and {len(storms) - 20} more storm regions")

    print(
        f"\nTotal: {len(storms)} storm regions, "
        f"{sum(s['depths'] for s in storms)} depths, "
        f"{sum(s['total_events'] for s in storms)} events"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Visualize DAG branching factor profiles"
    )
    parser.add_argument("profile", help="CSV profile from dagcmp --profile")
    parser.add_argument(
        "--threshold",
        type=float,
        default=2.0,
        help="BF threshold for storm detection (default: 2.0)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Save plot to file instead of showing interactively",
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Also show sliding-window heatmap",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=50,
        help="Sliding window size for heatmap (default: 50)",
    )
    parser.add_argument(
        "--storms-only",
        action="store_true",
        help="Only print storm table, no plots",
    )
    args = parser.parse_args()

    rows = load_profile(args.profile)
    if not rows:
        print("No data in profile", file=sys.stderr)
        sys.exit(1)

    total_events = sum(r.events for r in rows)
    total_prev = sum(r.prev_events for r in rows)
    avg_bf = total_prev / total_events if total_events else 0
    peak = max(rows, key=lambda r: r.bf)

    print(
        f"Profile: {len(rows)} depths, {total_events} events, "
        f"avg BF: {avg_bf:.3f}, peak BF: {peak.bf:.3f} @ depth {peak.depth}",
        file=sys.stderr,
    )

    storms = find_storms(rows, args.threshold)
    print_storm_table(storms, args.threshold)

    if args.storms_only:
        return

    plot_bf_over_depth(rows, args.threshold, args.output)

    if args.heatmap:
        plot_heatmap(rows, args.window, args.output)


if __name__ == "__main__":
    main()
