# monorepo-janky

A deliberately messy-but-working monorepo, for stress-testing the deploy planner.

```
monorepo-janky/
  package.json        <- root workspaces (api, dashboard) — the "is the root the app?" trap
  api/                <- Express backend
    app.js            <- binds 127.0.0.1 by default; health at /healthz (not /health)
    Dockerfile        <- at api/Dockerfile (root-relative path test)
  dashboard/          <- Vite + React frontend (static build)
    src/App.jsx       <- "Check backend health" button; hardcodes localhost under
                         VITE_BACKEND_BASE (non-standard env name)
```

## What it stresses
- **Monorepo split** — should become 2 components: `api` (service, dockerfile) +
  `dashboard` (static_site, `npm run build` → `dist`).
- **Root-relative dockerfile_path** — `api/Dockerfile`, source_dir `api`.
- **HOST=0.0.0.0 injection** — `app.js` defaults to `127.0.0.1`.
- **Name-agnostic backend-URL detection + $APP_URL rewrite** — frontend uses
  `VITE_BACKEND_BASE` (not the obvious name) and a localhost fallback; the
  planner must wire it to the deployed api.
- **Wrong health path** — backend health is `/healthz`.

## How to verify after deploy
1. The plan shows 2 components (api=service, dashboard=static_site).
2. `dashboard` env has `VITE_BACKEND_BASE = $APP_URL` (DO renders `${APP_URL}`).
3. Open the deployed dashboard, click **Check backend health** → status **ok**
   (proves frontend → backend wiring works end-to-end).

## Run locally
```
cd api && npm install && npm start          # http://127.0.0.1:5000/healthz
cd dashboard && npm install && npm run dev   # http://localhost:5173
```
