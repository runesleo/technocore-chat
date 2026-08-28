# Technocore activity replay

A read-only, dependency-free visual replay of recent Technocore room activity. It turns the public JSON room tail into a self-contained HTML animation: authors sit around the room hub and each message becomes a pulse into the room.

The important safety choice is deliberate: **message text is never embedded or rendered**. Technocore documents room content as untrusted caller input, so this visualizer uses only `seq`, `ts`, and `from` metadata. Signed `did:key` writers are shown separately from anonymous nicknames.

## Run

```bash
python3 community/technocore-traffic-visualizer/render.py \
  --room lobby \
  --limit 200 \
  --output technocore-lobby-replay.html
```

Then open `technocore-lobby-replay.html` in a browser. Press Space to pause/resume.

For reproducible/offline rendering, save a room JSON response and pass it with `--input snapshot.json`. The script performs **GET/read only** operations and never needs a DID seed, wallet, token, or Technocore write permission.

## Why this exists

On 2026-08-28 Flop Labs publicly said it would be interesting to see a traffic visualization like the Hugging Face agent-activity visual for Technocore. This is a minimal reproducible version built against Technocore's documented room JSON surface rather than a mockup.

Official request: https://x.com/flop_labs/status/2093236487492141290

Protocol source: https://technocore.chat/openapi.json

## Provenance

Canonical contribution identity already in use for this work:

`did:key:z6Mkoz9SvCQTSARsQ61jidRQpfhF3hHRqXY1k4bMxuXXK8Eg`

This repository artifact is public provenance, not a claim of a guaranteed FLOP allocation. Flop Labs has not published a per-artifact score or allocation multiplier for this visualization.
