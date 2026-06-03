# frontend-only-janky

A standalone frontend (vanilla Vite, no framework). Will become its own repo.

```
frontend-only-janky/
  index.html    <- "Check backend health" button
  main.js       <- backend URL from VITE_API_RUL (typo on purpose), localhost fallback
  package.json  <- vite build → dist
```

## What it stresses
- **Single static_site detection** — should be ONE `static_site` component,
  `npm run build` → `dist`. No invented backend.
- **The lone-frontend wiring question** — there's no backend in this repo, so
  the backend URL should become a **required env to fill** (the deployed
  backend-only-janky URL), NOT a same-app `$APP_URL` self-reference. Worth
  seeing what the planner does here.
- **Misspelled env name** (`VITE_API_RUL`) — name-agnostic, meaning-based
  detection of the backend base.
- **Vanilla JS** (no React) — different shape from the monorepo dashboard.

## How to verify after deploy
1. Plan = 1 static_site component.
2. Deploy **backend-only-janky** first; set this app's `VITE_API_RUL` to that
   live URL (or check what the planner proposed).
3. Open the deployed frontend, click **Check backend health** → status **ok**.

## Run locally
```
npm install
VITE_API_RUL=http://localhost:8000 npm run dev   # http://localhost:5173
```
