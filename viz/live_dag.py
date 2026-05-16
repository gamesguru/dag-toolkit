#!/usr/bin/env python3
"""Live DAG Viewer — real-time Matrix room DAG visualization.

Watches a JSONL file (from get-remote-dag / get-room-dag) and serves
a Cytoscape.js DAG graph over WebSocket, updating live as new events arrive.

Usage:
    # Watch an existing file:
    python live_dag.py /tmp/remote-dag-*.jsonl

    # Watch + poll via admin room (re-runs get-remote-dag periodically):
    python live_dag.py --room '!c10y-fNiMx5ijtgGFibzPUfNs9hpQvnJYPTV-fD2KPk' \
        --server starstruck.systems --limit 100 --poll 30

    # Then open http://localhost:9330
"""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone

import aiohttp
from aiohttp import web

# ─── Globals ─────────────────────────────────────────────────────────────────

CONNECTIONS: set[web.WebSocketResponse] = set()
EVENTS: dict[str, dict] = {}  # event_id -> event
LAST_HASH: str = ""
TAIL: int = 100  # sliding window


# ─── JSONL loading ───────────────────────────────────────────────────────────


def load_jsonl(path: str) -> list[dict]:
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


def file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── Graph building ─────────────────────────────────────────────────────────

# Server-based color palette
SERVER_COLORS = {}
PALETTE = [
    "#6c5ce7",
    "#00b894",
    "#e17055",
    "#0984e3",
    "#fdcb6e",
    "#e84393",
    "#00cec9",
    "#fab1a0",
    "#74b9ff",
    "#a29bfe",
    "#55efc4",
    "#ffeaa7",
    "#dfe6e9",
    "#fd79a8",
    "#636e72",
    "#81ecec",
    "#ff7675",
    "#b2bec3",
]


def server_color(server: str) -> str:
    if server not in SERVER_COLORS:
        idx = len(SERVER_COLORS) % len(PALETTE)
        SERVER_COLORS[server] = PALETTE[idx]
    return SERVER_COLORS[server]


def short_id(eid: str) -> str:
    return eid[:8] if len(eid) > 8 else eid


def event_type_color(ev: dict) -> str:
    etype = ev.get("type", "")
    if etype == "m.room.create":
        return "#2ecc71"
    if etype == "m.room.member":
        ms = ev.get("content", {}).get("membership", "")
        if ms == "join":
            return "#3498db"
        if ms == "leave":
            return "#e67e22"
        if ms == "ban":
            return "#e74c3c"
        return "#9b59b6"
    if etype == "m.room.message":
        return "#ecf0f1"
    if etype == "m.room.power_levels":
        return "#f39c12"
    if etype == "m.room.name":
        return "#1abc9c"
    return "#bdc3c7"


def build_graph_data(events: list[dict], tail: int = 100) -> dict:
    """Build Cytoscape.js elements from events, last `tail` by depth."""
    if not events:
        return {"nodes": [], "edges": []}

    # Sort by depth, take last N
    sorted_evs = sorted(events, key=lambda e: e.get("depth", 0))
    window = sorted_evs[-tail:] if len(sorted_evs) > tail else sorted_evs

    by_id = {ev["event_id"]: ev for ev in window if "event_id" in ev}
    id_set = set(by_id.keys())

    nodes = []
    edges = []

    for ev in window:
        eid = ev.get("event_id", "?")
        sender = ev.get("sender", "")
        server = sender.split(":")[1] if ":" in sender else "unknown"
        localpart = sender.split(":")[0].lstrip("@") if sender else "?"
        etype = ev.get("type", "").replace("m.room.", "")
        depth = ev.get("depth", 0)
        ts = ev.get("origin_server_ts", 0)
        content = ev.get("content", {})

        # Build label
        if ev.get("type") == "m.room.member":
            membership = content.get("membership", "?")
            sk = ev.get("state_key", "")
            sk_local = sk.split(":")[0].lstrip("@") if sk else ""
            label = f"{membership}:{sk_local}"
        elif ev.get("type") == "m.room.message":
            body = content.get("body", "")[:50]
            label = f"{localpart}: {body}" if body else etype
        elif ev.get("type") == "m.room.name":
            label = f"name: {content.get('name', '?')}"
        else:
            label = f"{etype} ({localpart})"

        ts_str = ""
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            ts_str = dt.strftime("%H:%M:%S")

        n_prev = len(ev.get("prev_events", []))

        nodes.append(
            {
                "data": {
                    "id": eid,
                    "label": label,
                    "short_id": short_id(eid),
                    "depth": depth,
                    "sender": sender,
                    "localpart": localpart,
                    "server": server,
                    "type": ev.get("type", ""),
                    "short_type": etype,
                    "ts": ts_str,
                    "ts_ms": ts,
                    "color": event_type_color(ev),
                    "border_color": server_color(server),
                    "n_prev": n_prev,
                    "full_id": eid,
                }
            }
        )

        for prev in ev.get("prev_events", []):
            if prev in id_set:
                edges.append(
                    {
                        "data": {
                            "source": prev,
                            "target": eid,
                        }
                    }
                )
            else:
                # Ghost node for external reference
                ghost_id = f"ghost_{prev}"
                nodes.append(
                    {
                        "data": {
                            "id": ghost_id,
                            "label": short_id(prev),
                            "short_id": short_id(prev),
                            "depth": depth - 1,
                            "sender": "",
                            "server": "external",
                            "type": "external",
                            "short_type": "ext",
                            "ts": "",
                            "color": "#555",
                            "border_color": "#333",
                            "n_prev": 0,
                            "ghost": True,
                            "full_id": prev,
                        }
                    }
                )
                edges.append(
                    {
                        "data": {
                            "source": ghost_id,
                            "target": eid,
                        }
                    }
                )

    # Deduplicate ghost nodes
    seen_ids = set()
    deduped_nodes = []
    for n in nodes:
        nid = n["data"]["id"]
        if nid not in seen_ids:
            seen_ids.add(nid)
            deduped_nodes.append(n)

    # Build server legend
    servers_seen = sorted(
        {
            n["data"]["server"]
            for n in deduped_nodes
            if n["data"]["server"] not in ("external", "unknown")
        }
    )
    legend = {s: server_color(s) for s in servers_seen}

    # Stats
    depths = [n["data"]["depth"] for n in deduped_nodes if not n["data"].get("ghost")]
    depth_range = f"{min(depths)}..{max(depths)}" if depths else "?"

    return {
        "nodes": deduped_nodes,
        "edges": edges,
        "legend": legend,
        "depth_range": depth_range,
        "event_count": len([n for n in deduped_nodes if not n["data"].get("ghost")]),
    }


# ─── HTML ────────────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live DAG — Matrix Room</title>
<script src="https://unpkg.com/cytoscape@3.30.4/dist/cytoscape.min.js"></script>
<script src="https://unpkg.com/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
<style>
  @import url(
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap'
  );

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: #0f0f13;
    color: #e0e0e0;
    overflow: hidden;
    height: 100vh;
  }

  #header {
    position: fixed; top: 0; left: 0; right: 0;
    height: 52px;
    background: linear-gradient(135deg, #1a1a24 0%, #12121a 100%);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 20px;
    z-index: 100;
    backdrop-filter: blur(20px);
  }

  #header h1 {
    font-size: 15px; font-weight: 600;
    background: linear-gradient(135deg, #6c5ce7, #a29bfe);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: -0.3px;
  }

  #status {
    font-size: 12px; color: #666;
    font-family: 'JetBrains Mono', monospace;
    display: flex; align-items: center; gap: 8px;
  }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #e74c3c;
    transition: background 0.3s;
  }
  .status-dot.connected { background: #2ecc71; box-shadow: 0 0 8px #2ecc7188; }

  #controls {
    display: flex; gap: 10px; align-items: center;
  }

  #controls button {
    font-family: 'Inter', sans-serif;
    font-size: 11px; font-weight: 500;
    padding: 5px 12px;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 6px;
    background: rgba(255,255,255,0.04);
    color: #aaa;
    cursor: pointer;
    transition: all 0.2s;
  }
  #controls button:hover {
    background: rgba(108,92,231,0.2);
    border-color: #6c5ce7;
    color: #fff;
  }

  #tail-input {
    width: 60px; padding: 4px 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 6px;
    color: #e0e0e0;
    text-align: center;
  }

  #cy {
    position: fixed;
    top: 52px; left: 0; right: 0; bottom: 0;
    background: #0f0f13;
  }

  #legend {
    position: fixed; bottom: 16px; left: 16px;
    background: rgba(20,20,30,0.92);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 12px 16px;
    z-index: 100;
    max-width: 280px;
    font-size: 11px;
  }

  #legend h3 {
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 1px;
    color: #666; margin-bottom: 8px;
  }

  .legend-item {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #aaa;
  }

  .legend-swatch {
    width: 12px; height: 12px;
    border-radius: 3px;
    flex-shrink: 0;
  }

  #tooltip {
    position: fixed;
    display: none;
    background: rgba(20,20,30,0.95);
    backdrop-filter: blur(16px);
    border: 1px solid rgba(108,92,231,0.3);
    border-radius: 10px;
    padding: 12px 16px;
    z-index: 200;
    max-width: 420px;
    font-size: 12px;
    line-height: 1.6;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    pointer-events: none;
  }

  #tooltip .tt-id {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px; color: #6c5ce7;
    word-break: break-all;
  }

  #tooltip .tt-label {
    font-weight: 600; color: #fff;
    margin: 4px 0;
  }

  #tooltip .tt-meta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px; color: #888;
  }

  #stats {
    position: fixed; bottom: 16px; right: 16px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px; color: #444;
    z-index: 100;
  }
</style>
</head>
<body>
<div id="header">
  <h1>⬡ Live DAG Viewer</h1>
  <div id="controls">
    <label style="font-size:11px;color:#666">Tail:</label>
    <input id="tail-input" type="number" value="100" min="10" max="5000">
    <button id="btn-fit">Fit</button>
    <button id="btn-layout">Re-layout</button>
  </div>
  <div id="status">
    <div class="status-dot" id="ws-dot"></div>
    <span id="status-text">Connecting...</span>
  </div>
</div>
<div id="cy"></div>
<div id="legend"></div>
<div id="tooltip"></div>
<div id="stats"></div>

<script>
const cy = cytoscape({
  container: document.getElementById('cy'),
  style: [
    {
      selector: 'node',
      style: {
        'label': 'data(short_id)',
        'text-valign': 'center',
        'text-halign': 'center',
        'font-size': '8px',
        'font-family': '"JetBrains Mono", monospace',
        'color': '#ddd',
        'text-outline-color': 'data(color)',
        'text-outline-width': 1.5,
        'background-color': 'data(color)',
        'border-color': 'data(border_color)',
        'border-width': 3,
        'width': 60,
        'height': 28,
        'shape': 'round-rectangle',
        'text-wrap': 'ellipsis',
        'text-max-width': '54px',
      }
    },
    {
      selector: 'node[?ghost]',
      style: {
        'background-color': '#1a1a24',
        'border-style': 'dashed',
        'border-color': '#333',
        'border-width': 1.5,
        'opacity': 0.5,
        'width': 40,
        'height': 20,
        'font-size': '7px',
      }
    },
    {
      selector: 'node[n_prev > 1]',
      style: {
        'border-width': 4,
        'width': 70,
      }
    },
    {
      selector: 'edge',
      style: {
        'width': 1.5,
        'line-color': 'rgba(120,120,150,0.3)',
        'target-arrow-color': 'rgba(120,120,150,0.4)',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'arrow-scale': 0.6,
      }
    },
    {
      selector: ':selected',
      style: {
        'border-color': '#fff',
        'border-width': 4,
      }
    }
  ],
  layout: { name: 'preset' },
  minZoom: 0.1,
  maxZoom: 4,
  wheelSensitivity: 0.3,
});

// Tooltip
const tooltip = document.getElementById('tooltip');
cy.on('mouseover', 'node', (e) => {
  const d = e.target.data();
  if (d.ghost) return;
  tooltip.innerHTML = `
    <div class="tt-id">${d.full_id}</div>
    <div class="tt-label">${escapeHtml(d.label)}</div>
    <div class="tt-meta">
      depth: ${d.depth} · ${d.ts}<br>
      sender: ${d.sender}<br>
      type: ${d.type}<br>
      prev_events: ${d.n_prev}
    </div>
  `;
  tooltip.style.display = 'block';
  const pos = e.renderedPosition || e.target.renderedPosition();
  tooltip.style.left = (pos.x + 20) + 'px';
  tooltip.style.top = (pos.y + 60) + 'px';
});
cy.on('mouseout', 'node', () => { tooltip.style.display = 'none'; });

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// WebSocket
let ws;
let reconnectTimer;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    document.getElementById('ws-dot').classList.add('connected');
    document.getElementById('status-text').textContent = 'Connected';
    const tail = parseInt(document.getElementById('tail-input').value) || 100;
    ws.send(JSON.stringify({type: 'set_tail', tail}));
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.type === 'graph') {
      updateGraph(data);
    }
  };

  ws.onclose = () => {
    document.getElementById('ws-dot').classList.remove('connected');
    document.getElementById('status-text').textContent = 'Disconnected — reconnecting...';
    reconnectTimer = setTimeout(connect, 2000);
  };
}

function updateGraph(data) {
  const elements = [...data.nodes, ...data.edges];

  // Batch update
  cy.batch(() => {
    cy.elements().remove();
    cy.add(elements);
  });

  // Layout
  cy.layout({
    name: 'dagre',
    rankDir: 'TB',
    nodeSep: 15,
    rankSep: 40,
    edgeSep: 8,
    animate: false,
    fit: true,
    padding: 30,
  }).run();

  // Legend
  const legendEl = document.getElementById('legend');
  if (data.legend && Object.keys(data.legend).length > 0) {
    let html = '<h3>Servers</h3>';
    for (const [server, color] of Object.entries(data.legend)) {
      html += `<div class="legend-item"><div class="legend-swatch" style="background:${color}"></div>${server}</div>`;
    }
    legendEl.innerHTML = html;
  }

  // Stats
  document.getElementById('stats').textContent =
    `${data.event_count} events · depth ${data.depth_range}`;
  document.getElementById('status-text').textContent =
    `Connected · ${data.event_count} events`;
}

// Controls
document.getElementById('btn-fit').onclick = () => cy.fit(null, 30);
document.getElementById('btn-layout').onclick = () => {
  cy.layout({
    name: 'dagre', rankDir: 'TB', nodeSep: 15, rankSep: 40,
    animate: true, animationDuration: 300, fit: true, padding: 30,
  }).run();
};
document.getElementById('tail-input').onchange = (e) => {
  const tail = parseInt(e.target.value) || 100;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({type: 'set_tail', tail}));
  }
};

connect();
</script>
</body>
</html>"""


# ─── Web server ──────────────────────────────────────────────────────────────


async def index_handler(request):
    return web.Response(text=HTML_PAGE, content_type="text/html")


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CONNECTIONS.add(ws)
    print(f"[ws] client connected ({len(CONNECTIONS)} total)")

    # Send current state immediately
    tail = TAIL
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "set_tail":
                    tail = max(10, min(5000, data.get("tail", 100)))
                    graph = build_graph_data(list(EVENTS.values()), tail)
                    await ws.send_json({"type": "graph", **graph})
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        CONNECTIONS.discard(ws)
        print(f"[ws] client disconnected ({len(CONNECTIONS)} total)")

    return ws


async def broadcast_graph():
    if not CONNECTIONS:
        return
    graph = build_graph_data(list(EVENTS.values()), TAIL)
    payload = json.dumps({"type": "graph", **graph})
    dead = set()
    for ws in CONNECTIONS:
        try:
            await ws.send_str(payload)
        except Exception:
            dead.add(ws)
    CONNECTIONS.difference_update(dead)


async def watch_file(path: str, interval: float = 1.0):
    """Watch a JSONL file for changes and broadcast updates."""
    global LAST_HASH, EVENTS

    while True:
        await asyncio.sleep(interval)

        if not os.path.exists(path):
            # Try glob pattern match
            from glob import glob

            matches = sorted(glob(path))
            if matches:
                path = matches[-1]
                print(f"[watch] resolved to: {path}")
            else:
                continue

        try:
            h = file_hash(path)
        except (OSError, IOError):
            continue

        if h == LAST_HASH:
            continue

        LAST_HASH = h
        events = load_jsonl(path)
        EVENTS = {ev["event_id"]: ev for ev in events if "event_id" in ev}
        print(f"[watch] reloaded {len(EVENTS)} events from {path}")
        await broadcast_graph()


async def watch_glob(pattern: str, interval: float = 2.0):
    """Watch for new files matching a glob pattern."""
    global LAST_HASH, EVENTS

    from glob import glob

    last_path = None

    while True:
        await asyncio.sleep(interval)

        matches = sorted(glob(pattern))
        if not matches:
            continue

        current = matches[-1]
        try:
            h = file_hash(current)
        except (OSError, IOError):
            continue

        if current == last_path and h == LAST_HASH:
            continue

        last_path = current
        LAST_HASH = h
        events = load_jsonl(current)
        EVENTS = {ev["event_id"]: ev for ev in events if "event_id" in ev}
        print(f"[watch] loaded {len(EVENTS)} events from {current}")
        await broadcast_graph()


async def start_background_tasks(app):
    path = app["watch_path"]
    if "*" in path or "?" in path:
        app["watcher"] = asyncio.create_task(watch_glob(path))
    else:
        app["watcher"] = asyncio.create_task(watch_file(path))


async def cleanup_background_tasks(app):
    app["watcher"].cancel()
    await app["watcher"]


def main():
    global TAIL

    parser = argparse.ArgumentParser(description="Live DAG Viewer")
    parser.add_argument(
        "jsonl",
        nargs="?",
        help="JSONL file to watch (supports globs like /tmp/remote-dag-*.jsonl)",
    )
    parser.add_argument(
        "--port", type=int, default=9330, help="HTTP port (default: 9330)"
    )
    parser.add_argument(
        "--tail", type=int, default=100, help="Events to show (default: 100)"
    )
    parser.add_argument(
        "--room",
        help="Room ID for earthtopic (auto-generates watch path)",
    )
    parser.add_argument("--server", help="Remote server name (for watch path)")
    args = parser.parse_args()

    TAIL = args.tail

    # Determine watch path
    if args.jsonl:
        watch_path = args.jsonl
    elif args.room:
        safe_room = args.room.replace("!", "").replace(":", "_")
        if args.server:
            watch_path = f"/tmp/remote-dag-{safe_room}-*-{args.server}*.jsonl"
        else:
            watch_path = f"/tmp/*-dag-{safe_room}-*.jsonl"
    else:
        # Default: earthtopic
        watch_path = "/tmp/*-dag-c10y_fNiMx5ijtgGFibzPUfNs9hpQvnJYPTV_fD2KPk-*.jsonl"

    print(f"[live_dag] Watching: {watch_path}")
    print(f"[live_dag] Tail: {TAIL} events")
    print(f"[live_dag] Open http://localhost:{args.port}")

    # Pre-load if file exists
    from glob import glob

    matches = sorted(glob(watch_path))
    if matches:
        events = load_jsonl(matches[-1])
        global EVENTS, LAST_HASH
        EVENTS = {ev["event_id"]: ev for ev in events if "event_id" in ev}
        LAST_HASH = file_hash(matches[-1])
        print(f"[live_dag] Pre-loaded {len(EVENTS)} events from {matches[-1]}")

    app = web.Application()
    app["watch_path"] = watch_path
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    web.run_app(app, host="0.0.0.0", port=args.port, print=None)
    print("[live_dag] Server stopped")


if __name__ == "__main__":
    main()
