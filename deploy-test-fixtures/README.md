# deploy-test-fixtures

Throwaway test projects for stressing the **deploy planner**. Each is meant to
be lifted out of this repo into its **own GitHub repo**, then deployed through
Ship to see how the planner copes.

They are deliberately **"janky but working"** — written the way a real person
in a hurry would (hardcoded localhost, odd folder names, misspelled env vars,
127.0.0.1 binds, health on `/healthz`), NOT pre-shaped to make the planner's
job easy. Each one DOES run locally and DOES deploy correctly *if the planner
does its job*. Every frontend has a **"Check backend health" button** so you
can confirm front↔back wiring end-to-end after deploy.

## The three fixtures

| Folder | Shape | Stack | Main thing it tests |
|---|---|---|---|
| `monorepo-janky/` | backend + frontend in one repo | Node/Express + Vite/React | monorepo split into 2 components; root-relative dockerfile; HOST injection; name-agnostic `$APP_URL` wiring (`VITE_BACKEND_BASE`); `/healthz` |
| `backend-only-janky/` | backend only | Python/FastAPI | single service, no invented frontend; different stack; HOST injection; `/health` |
| `frontend-only-janky/` | frontend only | vanilla Vite | single static_site; misspelled env (`VITE_API_RUL`); the lone-frontend "where's the backend URL?" question |

## Suggested test order
1. **monorepo-janky** — the headline case (split + wire + healthcheck button → ok).
2. **backend-only-janky** — confirm a single service deploys and `/health` passes.
3. **frontend-only-janky** — deploy it, point `VITE_API_RUL` at the backend-only
   live URL, confirm the button goes green. (Tests cross-repo wiring, which the
   planner can't do automatically — interesting to see what it proposes.)

## Notes
- `node_modules` / `dist` are NOT included — run `npm install` after splitting
  out. Each folder's README has local-run instructions + a per-fixture verify
  checklist.
- Nothing here is wired into Ship's build; it's inert until you move it out.
