#!/usr/bin/env python3
"""Export safe Technocore activity media from a room JSON response.

The source response may contain untrusted message text. This exporter never reads,
embeds, hashes per-message text, or renders it. Only room, seq, ts, and from are used.
Outputs are a static SVG, PNG poster, animated GIF, a metadata-only snapshot, and a
machine-readable manifest with source/output hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import pathlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover - CI installs Pillow for raster media
    raise SystemExit("Pillow is required for PNG/GIF export: python -m pip install Pillow") from exc

CANONICAL_DID = "did:key:z6Mkoz9SvCQTSARsQ61jidRQpfhF3hHRqXY1k4bMxuXXK8Eg"
SOURCE_URL = "https://technocore.chat/r/lobby?format=json&limit=200&wait=0"
WIDTH, HEIGHT = 1280, 720
MAX_AUTHORS = 28
FPS = 12
FRAMES = 96

BG = (7, 9, 13)
PANEL = (14, 18, 25)
TEXT = (239, 243, 248)
MUTED = (135, 146, 164)
LINE = (39, 48, 63)
SIGNED = (238, 244, 252)
ANON = (105, 116, 134)
ACCENT = (174, 252, 212)


@dataclass(frozen=True)
class Event:
    seq: int
    ts: str
    epoch: float
    author: str
    signed: bool


@dataclass(frozen=True)
class Node:
    key: str
    label: str
    signed: bool
    canonical: bool
    count: int
    x: float
    y: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=pathlib.Path, required=True)
    p.add_argument("--output-dir", type=pathlib.Path, required=True)
    p.add_argument("--source-url", default=SOURCE_URL)
    return p.parse_args()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_epoch(raw: str) -> float:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def load_events(raw: dict) -> tuple[str, list[Event]]:
    room = str(raw.get("room") or "lobby")
    events: list[Event] = []
    for item in raw.get("messages") or []:
        # SECURITY: deliberately access only these three fields. Never touch item["text"].
        try:
            seq = int(item["seq"])
            ts = str(item["ts"])
            author = str(item["from"])
            epoch = parse_epoch(ts)
        except (KeyError, TypeError, ValueError):
            continue
        events.append(Event(seq, ts, epoch, author, author.startswith("did:key:")))
    events.sort(key=lambda e: (e.epoch, e.seq))
    if not events:
        raise SystemExit("No valid seq/ts/from events found")
    return room, events


def label_for(index: int, signed: bool, canonical: bool) -> str:
    if canonical:
        return "CANONICAL DID"
    return f"{'DID' if signed else 'ANON'} {index:02d}"


def build_nodes(events: list[Event]) -> tuple[list[Node], dict[str, str]]:
    counts = Counter(e.author for e in events)
    ranked = [author for author, _ in counts.most_common(MAX_AUTHORS)]
    top = set(ranked)
    remap = {author: author for author in ranked}
    if len(counts) > MAX_AUTHORS:
        remap.update({author: "__other__" for author in counts if author not in top})
        ranked.append("__other__")
        counts["__other__"] = sum(counts[a] for a in counts if a not in top)

    cx, cy = WIDTH * 0.5, HEIGHT * 0.535
    radius = min(WIDTH, HEIGHT) * 0.335
    nodes: list[Node] = []
    for i, author in enumerate(ranked, start=1):
        signed = author.startswith("did:key:")
        canonical = author == CANONICAL_DID
        angle = -math.pi / 2 + (i - 1) * 2 * math.pi / max(1, len(ranked))
        jitter = ((int(hashlib.sha256(author.encode()).hexdigest()[:4], 16) % 17) - 8) * 1.8
        r = radius + jitter
        nodes.append(
            Node(
                key=author,
                label="OTHER" if author == "__other__" else label_for(i, signed, canonical),
                signed=signed,
                canonical=canonical,
                count=counts[author],
                x=cx + math.cos(angle) * r,
                y=cy + math.sin(angle) * r,
            )
        )
    return nodes, remap


def safe_snapshot(room: str, events: list[Event], source_url: str, source_sha: str) -> dict:
    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema": "technocore-activity-metadata-snapshot/v1",
        "source": {
            "url": source_url,
            "captured_at": captured_at,
            "raw_response_sha256": source_sha,
        },
        "room": room,
        "message_text_included": False,
        "fields_used": ["seq", "ts", "from"],
        "events": [
            {"seq": e.seq, "ts": e.ts, "from": e.author, "signed": e.signed} for e in events
        ],
    }


def stats(events: list[Event]) -> dict:
    duration = max(0.0, events[-1].epoch - events[0].epoch)
    signed = sum(e.signed for e in events)
    return {
        "messages": len(events),
        "authors": len({e.author for e in events}),
        "signed_messages": signed,
        "signed_share": signed / len(events),
        "window_seconds": duration,
        "messages_per_minute": len(events) * 60 / duration if duration else 0.0,
        "first_seq": events[0].seq,
        "last_seq": events[-1].seq,
        "first_ts": events[0].ts,
        "last_ts": events[-1].ts,
        "canonical_did_events": sum(e.author == CANONICAL_DID for e in events),
    }


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if pathlib.Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


F_TITLE = font(28, True)
F_SUB = font(15)
F_STAT = font(30, True)
F_SMALL = font(11)
F_HUB = font(16, True)


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    fnt: ImageFont.ImageFont,
    fill=TEXT,
    anchor=None,
) -> None:
    draw.text(xy, value, font=fnt, fill=fill, anchor=anchor)


def base_frame(room: str, nodes: list[Node], s: dict) -> Image.Image:
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(
        (22, 20, WIDTH - 22, HEIGHT - 20), radius=24, fill=PANEL, outline=LINE, width=1
    )
    text(d, (50, 43), f"TECHNOCORE /{room.upper()} ACTIVITY", F_TITLE)
    text(d, (50, 82), "PUBLIC METADATA REPLAY · MESSAGE TEXT EXCLUDED", F_SUB, MUTED)

    stat_x = [52, 250, 470, 720]
    stat_vals = [
        (str(s["messages"]), "EVENTS"),
        (str(s["authors"]), "AUTHORS"),
        (f"{s['signed_share'] * 100:.1f}%", "SIGNED"),
        (f"{s['messages_per_minute']:.1f}/MIN", "OBSERVED RATE"),
    ]
    for x0, (value, label) in zip(stat_x, stat_vals, strict=True):
        text(d, (x0, 122), value, F_STAT)
        text(d, (x0, 159), label, F_SMALL, MUTED)

    cx, cy = WIDTH * 0.5, HEIGHT * 0.535
    for node in nodes:
        d.line((node.x, node.y, cx, cy), fill=LINE, width=1)
    for node in nodes:
        radius = max(4, min(12, 4 + int(math.log2(1 + node.count))))
        fill = ACCENT if node.canonical else SIGNED if node.signed else ANON
        d.ellipse((node.x - radius, node.y - radius, node.x + radius, node.y + radius), fill=fill)
        text(d, (node.x, node.y + radius + 9), node.label, F_SMALL, MUTED, "ma")

    d.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), outline=SIGNED, width=3)
    d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), outline=LINE, width=1)
    text(d, (cx, cy + 1), f"/{room}", F_HUB, TEXT, "mm")

    footer = f"SEQ {s['first_seq']} → {s['last_seq']}  ·  {s['window_seconds']:.1f}s WINDOW  ·  CONTRIBUTION PROVENANCE: z6Mkoz…XK8Eg"
    text(d, (52, HEIGHT - 52), footer, F_SMALL, MUTED)
    return im


def point_on_edge(node: Node, progress: float) -> tuple[float, float]:
    cx, cy = WIDTH * 0.5, HEIGHT * 0.535
    eased = 1 - (1 - progress) ** 3
    return node.x + (cx - node.x) * eased, node.y + (cy - node.y) * eased


def draw_pulses(
    im: Image.Image,
    events: list[Event],
    nodes_by_key: dict[str, Node],
    remap: dict[str, str],
    frame: int,
) -> None:
    d = ImageDraw.Draw(im)
    total = len(events)
    current = int(frame / FRAMES * total)
    trail = 18
    for event_index in range(max(0, current - trail), min(total, current + 1)):
        age = current - event_index
        local = max(0.0, min(1.0, 1.0 - age / trail))
        progress = (frame * total / FRAMES - event_index) / max(1.0, total / FRAMES * 5.0)
        progress = max(0.0, min(1.0, progress))
        key = remap.get(events[event_index].author, events[event_index].author)
        node = nodes_by_key.get(key)
        if not node:
            continue
        px, py = point_on_edge(node, progress)
        radius = 4 if events[event_index].signed else 3
        glow = int(4 + 7 * local)
        color = (
            ACCENT
            if events[event_index].author == CANONICAL_DID
            else SIGNED
            if events[event_index].signed
            else ANON
        )
        d.ellipse(
            (px - glow, py - glow, px + glow, py + glow),
            outline=tuple(int(c * 0.45) for c in color),
            width=1,
        )
        d.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color)
    if total:
        shown = min(total, current + 1)
        text(d, (WIDTH - 50, HEIGHT - 52), f"REPLAY {shown:03d}/{total:03d}", F_SMALL, MUTED, "ra")


def render_svg(
    room: str, nodes: list[Node], events: list[Event], remap: dict[str, str], s: dict
) -> str:
    cx, cy = WIDTH * 0.5, HEIGHT * 0.535

    def esc(v: object) -> str:
        return html.escape(str(v), quote=True)

    lines, circles, labels, pulses = [], [], [], []
    node_index = {n.key: i for i, n in enumerate(nodes)}
    for n in nodes:
        lines.append(
            f'<line x1="{n.x:.1f}" y1="{n.y:.1f}" x2="{cx:.1f}" y2="{cy:.1f}" class="spoke"/>'
        )
        cls = "canonical" if n.canonical else "signed" if n.signed else "anon"
        radius = max(4, min(12, 4 + int(math.log2(1 + n.count))))
        circles.append(f'<circle cx="{n.x:.1f}" cy="{n.y:.1f}" r="{radius}" class="node {cls}"/>')
        labels.append(
            f'<text x="{n.x:.1f}" y="{n.y + radius + 18:.1f}" class="node-label" text-anchor="middle">{esc(n.label)}</text>'
        )
    for i, e in enumerate(events):
        key = remap.get(e.author, e.author)
        n = nodes[node_index[key]]
        cls = "canonical" if e.author == CANONICAL_DID else "signed" if e.signed else "anon"
        begin = (i / max(1, len(events))) * 8.0
        pulses.append(
            f'<circle r="{4 if e.signed else 3}" class="pulse {cls}" opacity="0">'
            f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.08;0.86;1" dur="0.9s" begin="{begin:.3f}s;loop.begin+{begin:.3f}s"/>'
            f'<animateMotion path="M {n.x:.1f} {n.y:.1f} L {cx:.1f} {cy:.1f}" dur="0.9s" begin="{begin:.3f}s;loop.begin+{begin:.3f}s" fill="freeze"/>'
            f"</circle>"
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">
<title id="title">Technocore /{esc(room)} activity replay</title><desc id="desc">Animation built from seq, timestamp and author metadata only. Message text is excluded.</desc>
<style>svg{{background:#07090d;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}.panel{{fill:#0e1219;stroke:#27303f}}.title{{fill:#eff3f8;font-size:28px;font-weight:700}}.sub,.label,.node-label,.foot{{fill:#8792a4}}.sub{{font-size:15px}}.stat{{fill:#eff3f8;font-size:30px;font-weight:700}}.label,.foot,.node-label{{font-size:11px}}.spoke{{stroke:#27303f;stroke-width:1}}.node.signed,.pulse.signed{{fill:#eef4fc}}.node.anon,.pulse.anon{{fill:#697486}}.node.canonical,.pulse.canonical{{fill:#aefcd4}}.hub{{fill:none;stroke:#eef4fc;stroke-width:3}}</style>
<rect x="22" y="20" width="1236" height="680" rx="24" class="panel"/><text x="50" y="67" class="title">TECHNOCORE /{esc(room.upper())} ACTIVITY</text><text x="50" y="91" class="sub">PUBLIC METADATA REPLAY · MESSAGE TEXT EXCLUDED</text>
<text x="52" y="145" class="stat">{s["messages"]}</text><text x="52" y="169" class="label">EVENTS</text><text x="250" y="145" class="stat">{s["authors"]}</text><text x="250" y="169" class="label">AUTHORS</text><text x="470" y="145" class="stat">{s["signed_share"] * 100:.1f}%</text><text x="470" y="169" class="label">SIGNED</text><text x="720" y="145" class="stat">{s["messages_per_minute"]:.1f}/MIN</text><text x="720" y="169" class="label">OBSERVED RATE</text>
{"".join(lines)}{"".join(circles)}{"".join(labels)}<circle cx="{cx}" cy="{cy}" r="40" class="hub"/><text x="{cx}" y="{cy + 5}" text-anchor="middle" class="title" style="font-size:16px">/{esc(room)}</text>{"".join(pulses)}
<text x="52" y="672" class="foot">SEQ {s["first_seq"]} → {s["last_seq"]} · {s["window_seconds"]:.1f}s WINDOW · CONTRIBUTION PROVENANCE: z6Mkoz…XK8Eg</text><circle id="loop" cx="0" cy="0" r="0"><animate attributeName="r" values="0;0" dur="8s" begin="0s;loop.end"/></circle></svg>'''


def save_gif(
    base: Image.Image,
    events: list[Event],
    nodes: list[Node],
    remap: dict[str, str],
    out: pathlib.Path,
) -> None:
    nodes_by_key = {n.key: n for n in nodes}
    frames: list[Image.Image] = []
    for frame in range(FRAMES):
        im = base.copy()
        draw_pulses(im, events, nodes_by_key, remap, frame)
        frames.append(
            im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        )
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=round(1000 / FPS),
        loop=0,
        optimize=True,
        disposal=2,
    )


def main() -> None:
    args = parse_args()
    raw_bytes = args.input.read_bytes()
    raw = json.loads(raw_bytes)
    room, events = load_events(raw)
    nodes, remap = build_nodes(events)
    s = stats(events)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = args.output_dir / "lobby-latest.metadata.json"
    svg_path = args.output_dir / "lobby-activity.svg"
    png_path = args.output_dir / "lobby-activity.png"
    gif_path = args.output_dir / "lobby-activity.gif"
    manifest_path = args.output_dir / "manifest.json"

    snapshot = safe_snapshot(room, events, args.source_url, sha256_bytes(raw_bytes))
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    svg_path.write_text(render_svg(room, nodes, events, remap, s), encoding="utf-8")
    base = base_frame(room, nodes, s)
    draw_pulses(base, events, {n.key: n for n in nodes}, remap, FRAMES - 1)
    base.save(png_path, optimize=True)
    save_gif(base_frame(room, nodes, s), events, nodes, remap, gif_path)

    outputs = {}
    for p in (snapshot_path, svg_path, png_path, gif_path):
        blob = p.read_bytes()
        outputs[p.name] = {"sha256": sha256_bytes(blob), "bytes": len(blob)}
    manifest = {
        "schema": "technocore-traffic-visualizer-manifest/v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": snapshot["source"],
        "safety": {
            "message_text_included": False,
            "fields_used": ["seq", "ts", "from"],
            "network_write_performed": False,
        },
        "identity": {
            "canonical_did": CANONICAL_DID,
            "canonical_did_events_in_window": s["canonical_did_events"],
        },
        "stats": s,
        "outputs": outputs,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
