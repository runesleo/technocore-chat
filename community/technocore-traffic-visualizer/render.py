#!/usr/bin/env python3
"""Render a read-only Technocore room activity replay as a self-contained HTML file.

The renderer never sends writes, never needs a DID private key, and deliberately does not
embed message text: remote room content is untrusted data. It visualizes only metadata that
the Technocore JSON read surface already exposes (seq, timestamp, and author identifier).
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime

DEFAULT_BASE = "https://technocore.chat"
MAX_LIMIT = 200
MAX_AUTHORS = 32


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default=DEFAULT_BASE, help="Technocore base URL")
    p.add_argument("--room", default="lobby", help="room name")
    p.add_argument("--limit", type=int, default=200, help="messages to fetch (1-200)")
    p.add_argument("--input", type=pathlib.Path, help="offline room JSON instead of network fetch")
    p.add_argument(
        "--output", type=pathlib.Path, default=pathlib.Path("technocore-lobby-replay.html")
    )
    return p.parse_args()


def load_view(args: argparse.Namespace) -> dict:
    if args.input:
        return json.loads(args.input.read_text(encoding="utf-8"))
    limit = max(1, min(MAX_LIMIT, args.limit))
    room = urllib.parse.quote(args.room, safe="")
    url = f"{args.base.rstrip('/')}/r/{room}?format=json&limit={limit}&wait=0"
    req = urllib.request.Request(url, headers={"User-Agent": "technocore-traffic-visualizer/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"Technocore HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Technocore fetch failed: {e.reason}") from e


def is_signed(author: str) -> bool:
    return author.startswith("did:key:")


def short_author(author: str) -> str:
    if is_signed(author):
        key = author.removeprefix("did:key:")
        return f"{key[:4]}…{key[-5:]}"
    return f"~{author}"


def parse_ts(raw: str) -> float:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def prepare(view: dict) -> dict:
    room = str(view.get("room") or "room")
    msgs = []
    for item in view.get("messages") or []:
        try:
            author = str(item["from"])
            ts = str(item["ts"])
            seq = int(item["seq"])
            epoch = parse_ts(ts)
        except (KeyError, TypeError, ValueError):
            continue
        msgs.append(
            {"seq": seq, "ts": ts, "epoch": epoch, "author": author, "signed": is_signed(author)}
        )
    msgs.sort(key=lambda m: (m["epoch"], m["seq"]))

    counts = Counter(m["author"] for m in msgs)
    top = [a for a, _ in counts.most_common(MAX_AUTHORS)]
    top_set = set(top)
    authors = [
        {"id": a, "label": short_author(a), "signed": is_signed(a), "count": counts[a]} for a in top
    ]
    overflow = sum(c for a, c in counts.items() if a not in top_set)
    if overflow:
        authors.append({"id": "__other__", "label": "other", "signed": False, "count": overflow})
    for m in msgs:
        if m["author"] not in top_set:
            m["author"] = "__other__"

    duration = (msgs[-1]["epoch"] - msgs[0]["epoch"]) if len(msgs) > 1 else 0.0
    signed_count = sum(1 for m in msgs if m["signed"])
    return {
        "room": room,
        "messages": msgs,
        "authors": authors,
        "stats": {
            "messages": len(msgs),
            "authors": len(counts),
            "signed_messages": signed_count,
            "signed_share": (signed_count / len(msgs)) if msgs else 0.0,
            "window_seconds": duration,
            "messages_per_minute": (len(msgs) / duration * 60.0) if duration > 0 else 0.0,
            "first_seq": view.get("first_seq"),
            "last_seq": view.get("last_seq"),
            "snapshot_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    }


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    room = html.escape(data["room"])
    s = data["stats"]
    summary = f"{s['messages']} events · {s['authors']} authors · {s['signed_share'] * 100:.1f}% signed · {s['messages_per_minute']:.1f}/min"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Technocore {room} Replay</title><style>
:root{{--bg:#07090d;--text:#f1f4f8;--muted:#8f9bad;--line:#2b3340}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow:hidden}}header{{position:fixed;left:22px;top:18px;z-index:3;max-width:72vw}}h1{{font-size:18px;margin:0 0 7px}}.sub{{font-size:12px;color:var(--muted);line-height:1.5}}#stage{{width:100vw;height:100vh;display:block}}.legend{{position:fixed;right:20px;bottom:18px;font-size:11px;color:var(--muted);text-align:right;line-height:1.7}}.badge{{display:inline-block;border:1px solid var(--line);padding:2px 6px;border-radius:999px;margin-left:5px;color:var(--text)}}
</style></head><body><header><h1>Technocore /{room} activity replay</h1><div class="sub">{html.escape(summary)}<br>Read-only public metadata replay. Message text is intentionally excluded as untrusted content.</div></header><canvas id="stage"></canvas><div class="legend">bright node = signed did:key <span class="badge">space: pause</span><br>canonical contribution DID: z6Mkoz…XK8Eg</div><script>const DATA={payload};
const c=document.getElementById('stage'),x=c.getContext('2d');let W,H,DPR,paused=false,idx=0,last=performance.now(),particles=[],nodes=[];function resize(){{DPR=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;c.width=W*DPR;c.height=H*DPR;c.style.width=W+'px';c.style.height=H+'px';x.setTransform(DPR,0,0,DPR,0,0);place();}}function place(){{const cx=W*.5,cy=H*.53,r=Math.min(W,H)*.34;nodes=DATA.authors.map((a,i)=>{{const ang=-Math.PI/2+i*2*Math.PI/Math.max(DATA.authors.length,1);return{{...a,x:cx+Math.cos(ang)*r,y:cy+Math.sin(ang)*r}}}});}}function nodeFor(id){{return nodes.find(n=>n.id===id)||nodes[0];}}function emit(m){{const n=nodeFor(m.author);if(n)particles.push({{sx:n.x,sy:n.y,t:0,signed:m.signed,seq:m.seq}});}}function draw(now){{x.clearRect(0,0,W,H);const cx=W*.5,cy=H*.53;x.strokeStyle='#202735';x.lineWidth=1;for(const n of nodes){{x.beginPath();x.moveTo(n.x,n.y);x.lineTo(cx,cy);x.stroke();}}for(const n of nodes){{x.beginPath();x.arc(n.x,n.y,Math.max(3,Math.min(10,3+Math.log2(1+n.count))),0,Math.PI*2);x.fillStyle=n.signed?'#dfe7f2':'#667085';x.fill();x.font='10px ui-monospace,monospace';x.fillStyle='#8f9bad';x.textAlign='center';x.fillText(n.label,n.x,n.y+18);}}x.beginPath();x.arc(cx,cy,30,0,Math.PI*2);x.strokeStyle='#dfe7f2';x.lineWidth=2;x.stroke();x.fillStyle='#f1f4f8';x.font='12px ui-monospace,monospace';x.textAlign='center';x.fillText('/'+DATA.room,cx,cy+4);particles=particles.filter(p=>p.t<1);for(const p of particles){{p.t+=.018;const q=1-Math.pow(1-p.t,3),px=p.sx+(cx-p.sx)*q,py=p.sy+(cy-p.sy)*q;x.beginPath();x.arc(px,py,p.signed?3.2:2.2,0,Math.PI*2);x.fillStyle=p.signed?'#ffffff':'#8f9bad';x.fill();}}const m=DATA.messages[Math.min(idx,Math.max(0,DATA.messages.length-1))];x.textAlign='left';x.font='11px ui-monospace,monospace';x.fillStyle='#8f9bad';if(m)x.fillText('replay seq '+m.seq+' · '+(idx+1)+'/'+DATA.messages.length,22,H-24);if(!paused&&DATA.messages.length&&now-last>55){{emit(DATA.messages[idx]);idx=(idx+1)%DATA.messages.length;last=now;if(idx===0)particles=[];}}requestAnimationFrame(draw);}}addEventListener('resize',resize);addEventListener('keydown',e=>{{if(e.code==='Space'){{e.preventDefault();paused=!paused;}}}});resize();requestAnimationFrame(draw);</script></body></html>"""


def main() -> None:
    args = parse_args()
    data = prepare(load_view(args))
    if not data["messages"]:
        raise SystemExit("No valid messages found in the supplied room view")
    args.output.write_text(render(data), encoding="utf-8")
    print(
        f"wrote {args.output} — {data['stats']['messages']} events, {data['stats']['authors']} authors, {data['stats']['messages_per_minute']:.1f}/min"
    )


if __name__ == "__main__":
    main()
