# backend-only-janky

A standalone FastAPI backend (no frontend). Will become its own repo.

```
backend-only-janky/
  src/main.py       <- FastAPI; health at /health, CORS open
  requirements.txt  <- loose pins (no versions) — realistic human jank
  Dockerfile        <- uvicorn defaults to 127.0.0.1 unless HOST is set
```

## What it stresses
- **Single service detection** — should be ONE `service` component, no invented
  frontend.
- **Different stack** — Python/uvicorn (vs the monorepo's Node), so the planner
  isn't just pattern-matching Node.
- **HOST=0.0.0.0 injection** — Dockerfile CMD reads `${HOST:-127.0.0.1}`.
- **Health path** — `/health`.

## How to verify after deploy
1. Plan = 1 service component, dockerfile at repo root, `HOST=0.0.0.0` present.
2. `GET <live_url>/health` → `{"ok": true, ...}` (Ship's health probe should
   pass once DNS settles).

This pairs with **frontend-only-janky**: deploy this first, then point the
frontend at this app's live URL.

## Run locally
```
pip install -r requirements.txt
HOST=0.0.0.0 uvicorn src.main:app --port 8000   # http://localhost:8000/health
```
