# Deployments — WIP / handoff notes

Branch: `vv-deployments`. Working notes for the DigitalOcean deploy feature +
deploy planner. Update this as we go (this is the "where are we" doc).

## Current focus
Deploy **planner robustness** — turning "any repo" into a correct DO App
Platform deploy without the operator hand-fixing things.

## Status — monorepo deploy works end-to-end ✅
Test repo `WonnaBeCodeFather/monorepotest` (Express backend + Vite/React
frontend) deploys to DigitalOcean and reaches **Healthy**. The chain that got
it there (each was a real bug we fixed):
1. **Ship GitHub App needs the `Actions` permission** to dispatch the
   `ship-deploy-plan.yml` workflow (or use a manual LLM key to plan on the
   backend, bypassing Actions entirely).
2. **`dockerfile_path` is root-relative on DO** — DO adapter now joins
   `source_dir` (`_root_relative_dockerfile`).
3. **`HOST=0.0.0.0`** — planner injects it (env-configurable bind host) so the
   container listens on all interfaces, not localhost.
4. **Monorepo detection** — planner sees the full file tree + manifest layout
   map + a step-by-step "connectivity" prompt → splits into backend(service) +
   frontend(static_site) with correct `source_dir`s.

## Planner architecture (the "as-close-to-silver-bullet" combo)
- **Semantic LLM** — reads component source (incl. frontend entry files), finds
  things by MEANING not by fixed names (handles arbitrary/misspelled env vars).
- **Verify-guard + retry** (`_verify_plan` in `planner.py`) — deterministic
  safety net checked against KNOWN repo facts; on a miss it retries the model
  with a concrete correction. Catches: docker pointing at a non-existent
  Dockerfile, non-existent `source_dir`, and a frontend still hardcoding a
  `localhost` backend URL that wasn't redirected to `$APP_URL`.
- **`$APP_URL` token** — provider-neutral in the plan; the DO adapter renders it
  to `${APP_URL}` (`_render_env_value`). Used to point a frontend's build-time
  API base at the same deployed domain.

Files: `apps/backend/app/services/deploy/planner.py` (prompt, file collection,
verify), `.../providers/digitalocean.py` (spec builder, dockerfile path, env
value rendering), `.../model_catalog.py` (model list, defaults).

## OPEN — next problem to confirm
Just added the **frontend→backend connectivity** pass (`$APP_URL` + verify).
**Next: redeploy `monorepotest`** and check:
- the Plan shows `frontend` env `VITE_API_URL = $APP_URL` (DO renders `${APP_URL}`),
- the frontend "healthcheck" button works (was hitting `http://localhost:3001`).

## Next steps (rough order)
1. ✅/verify the connectivity fix (redeploy monorepotest).
2. **Deploy versioning** — history of deploy attempts per app, surface the
   plan + status per version, allow rollback / re-deploy a previous version.
   (Today the AppCard already groups by app with a History tab — extend it.)
3. **Route/prefix coherence** — couple routing (`/api`, `preserve_path_prefix`)
   with the wired URL so frontend↔backend paths always match.
4. **Internal wiring** — server→server, `DATABASE_URL`, queues (internal
   hostnames, not `$APP_URL`).
5. **Actions-workflow parity** — `ship-deploy-plan.yml` has its own inline
   schema/prompt; mirror the planner improvements (HOST, monorepo, dockerfile,
   connectivity) + bump bundle version + re-seed.
6. **Show the plan in the UI** — component breakdown (name · kind · source_dir ·
   runtime · env) so the operator can sanity-check before/after deploy.
7. **Health-probe grace period** — don't mark a fresh deploy "failing" before
   DNS/propagation settles.
8. Friendly error translation for the remaining DO/GitHub failure classes.

## Notes / gotchas
- Backend isn't volume-mounted in docker-compose → after backend edits:
  `docker compose build ship-server && docker compose up -d ship-server`.
- Two planning paths: **manual key** → synchronous backend planning (current);
  **no key** → dispatch `ship-deploy-plan.yml` in GitHub Actions (needs the
  Actions permission + the workflow registered on the repo).
- `*.ondigitalocean.app` DNS can lag locally (negative cache on router/ISP);
  use 1.1.1.1/8.8.8.8 or wait — not a deploy problem.
