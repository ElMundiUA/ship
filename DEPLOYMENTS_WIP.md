# Deployments — WIP / handoff notes

Branch: `vv-deployments`. Working notes for the DigitalOcean deploy feature +
deploy planner. Update this as we go (this is the "where are we" doc).

## Current focus
Deploy **planner robustness** — turning "any repo" into a correct DO App
Platform deploy without the operator hand-fixing things.

## ✅ Done this autonomous session — NEEDS VVLAD REVIEW (hand-test, NOT committed)
All of the below is in the working tree on `vv-deployments`, **uncommitted**.
Backend was rebuilt (`docker compose build ship-server`) and unit-checked;
console `tsc --noEmit` is clean. The *live* visual + real-DO confirmations are
intentionally left for vvlad (see "Phase 5" below).

1. **Plan breakdown in the UI** (`deployments-client.tsx` Components row +
   `deploy.py` `ApiDeployComponentOut` / `_plan_components_out`). The AppCard
   Overview now lists each planned component: name · kind · source_dir ·
   runtime · port · routes · env chips (secret values masked to `•••`). So the
   operator can sanity-check what the planner decided.
2. **Health-probe grace period** (`health.py` `probe_with_grace`,
   `HEALTH_GRACE_SECONDS=180`; wired into both probe sites in `deploy.py`). A
   just-Active app that isn't reachable yet (DNS/TLS/cold start) shows
   **pending** (yellow), not a scary **failing**, for the first 3 min after
   `finished_at`. A positive probe still flips to healthy immediately. FE already
   renders `healthy===null` as "pending" — no FE change needed.
3. **Friendly error translation** (`deploy.py`: `_classify_do_failure`,
   `_error_kind_for`, canonical hint constants + `error_kind`). DO/GitHub
   failures now get plain-language, actionable messages + a coarse `error_kind`
   the console can branch on. Covered kinds: `github_access`, `actions_access`,
   `workflow_unregistered` (dispatch 422), `workflow_missing`, `build_failed`,
   `health_check_failed`. Error row now renders multi-line (`whitespace-pre-line`)
   so DO's raw detail shows under the friendly summary.
4. **Cost estimate base** (`integrations/digitalocean/client.py`
   `propose_app` + `propose_monthly_cost`; DO adapter calls `/v2/apps/propose`
   best-effort at submit and stashes `estimated_monthly_usd` in
   `provider_ref.extra`; `deploy.py` exposes it on `ApiDeploymentOut`; FE shows a
   `≈ $X/mo` pill in the AppCard header + an "Est. cost" Row labelled as DO's
   approximate estimate). **Shows DO's own number only — no invented formulas.**
   Propose failure never blocks a deploy (we just show no number).
   - NOTE: `propose_monthly_cost` reads `app_cost` (DO's documented field) with
     a couple of fallbacks. Confirm against a real propose response during the
     first deploy — if DO's field name differs, add it to `_COST_KEYS`.

- **Test fixtures** under `deploy-test-fixtures/` (see step 12). Three janky-but-
  working projects to stress the planner; for vvlad to split into their own repos.

### Live dogfood fixes (2026-06-03, with vvlad watching) — also uncommitted
Surfaced while vvlad redeployed `monorepotest`:
- **🎉 Connectivity CONFIRMED (Phase 5, step 1):** the frontend's "healthcheck"
  button reached the backend on the deployed app — yesterday's `localhost`
  problem is gone. The planner's `$APP_URL` wiring works end-to-end, live.
- **Health probe hit the wrong path → false "failing"** (`health.py`). For a
  monorepo the backend sits behind route `/api` (DO strips the prefix), so its
  health is reachable at `/api/health`, but `health_check_path` returned bare
  `/health` → that lands on the frontend static site → 404 → "failing" on a
  healthy app. Fixed: `health_check_path` now prefixes the component's route
  (`_join_route`), e.g. `/api` + `/health` → `/api/health`. Verified live:
  `monorepotest` flipped to `healthy=True`. Backend rebuilt.
- **"Authorize on GitHub" button → DO 401** (`deployments-client.tsx`). It
  pointed at `cloud.digitalocean.com/apps/github/install`, which needs a DO
  *web session* and returns `{"id":"Unauthorized"}` without one (Ship's DO
  connection is a backend OAuth token — a different auth context; verified the
  stored token is valid via `GET /v2/account → 200`). Fixed: point at GitHub's
  own install page `github.com/apps/digitalocean/installations/new` (verified
  302 → GitHub login; user already has a GitHub session). Hot-reloads.
- **Auto-recheck ("emulate polling")** (`deployments-client.tsx`). Polling was
  removed earlier by request, so a just-active card stayed yellow until a
  manual re-check. Added a bounded auto-recheck in `AppCard`: when a deploy is
  active and health isn't green yet, fire `onRecheck` at 20/50/95/150/210s then
  stop (last attempt is past the 180s grace, so a truly-down app still ends up
  "failing"). No continuous polling. tsc clean.

### Diagnosis note: the "deploy failed with a workflow error" was NOT a bug
vvlad hit a `workflow_unregistered` (422) on a redeploy. Root cause (DB-confirmed):
the deploy took the **GitHub Actions planner path** because no manual LLM key was
pasted that time (the key path is `body.llm_api_key` → backend planning; without
it, it dispatches `ship-deploy-plan.yml`, which isn't registered on `monorepotest`
→ 422). DO was never contacted. The flow is **Actions-first by design**; the manual
key is a test-only path for now (may be removed later, leaving only Actions). So
the planner step is intentionally left as-is (vvlad's call). Yesterday's successful
deploys used the manual key (backend path); today's failure was simply no key.

## ⚠️ DON'T FORGET — sync the Actions pipeline once the manual path is solid
All planner fixes so far (monorepo detection, `HOST=0.0.0.0`, root-relative
`dockerfile_path`, `$APP_URL` connectivity, verify-guard + retry, model
defaults) live in the **backend/manual path** (`services/deploy/planner.py`).
The **GitHub Actions pipeline** `ship-deploy-plan.yml` has its OWN inline
schema + prompt and is currently BEHIND. Once the manual version is proven to
work end-to-end, **port every planner improvement into `ship-deploy-plan.yml`**,
bump the seed bundle version, and re-seed repos — otherwise the default (no
manual key) Actions path will produce worse plans than the manual one. This is
the #1 follow-up; see step 5.

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

## OPEN — Phase 5 (DO WITH VVLAD — needs a real deploy; do not run solo)
This is the live-confirm pass for everything above. Steps for vvlad:
1. **Redeploy `monorepotest`** (the existing test repo) and check:
   - Plan/Components row shows `frontend` env `VITE_API_URL = $APP_URL`
     (DO renders `${APP_URL}`); backend has `HOST=0.0.0.0`.
   - The frontend "healthcheck" button works (was hitting `http://localhost:3001`).
   - The **`≈ $X/mo` cost pill** appears (confirms `/v2/apps/propose` returned a
     number; if not, check `_COST_KEYS` vs the real propose response — see step 4).
   - During the first ~3 min the Health shows **pending**, not failing (grace).
2. **Run the 3 new fixtures** (`deploy-test-fixtures/`, see step 12) end-to-end,
   each as its own repo, to stress the planner on shapes it hasn't seen.
3. **Force a failure** (e.g. a no-Actions-permission private repo, or a repo
   with a broken Dockerfile) to eyeball the friendly error messages + the
   per-kind copy.

## Next steps (rough order)
1. ✅/verify the connectivity fix (redeploy monorepotest).
2. **Deploy versioning** — history of deploy attempts per app, surface the
   plan + status per version, allow rollback / re-deploy a previous version.
   (Today the AppCard already groups by app with a History tab — extend it.)
3. **Route/prefix coherence** — couple routing (`/api`, `preserve_path_prefix`)
   with the wired URL so frontend↔backend paths always match.
4. **Internal wiring** — server→server, `DATABASE_URL`, queues (internal
   hostnames, not `$APP_URL`).
5. **Actions-workflow parity (DO AFTER the manual path is solid — see the
   ⚠️ callout at top).** `ship-deploy-plan.yml` has its own inline
   schema/prompt and is behind the backend planner. Port EVERY improvement:
   monorepo detection (file tree + manifest layout), step-by-step connectivity
   prompt, `HOST=0.0.0.0`, root-relative `dockerfile_path`, `$APP_URL` wiring,
   verify-guard equivalent, model defaults. Then bump the seed bundle version
   and re-seed repos so the Actions path matches the manual one.
6. ✅ **Show the plan in the UI** — DONE this session (Components row). Possible
   polish later: collapse long env lists, show on the Deploy step pre-submit.
7. ✅ **Health-probe grace period** — DONE this session (`probe_with_grace`).
8. ✅ **Friendly error translation** — DONE this session (`_classify_do_failure`
   + `error_kind`). Remaining DO failure shapes pass through verbatim; extend
   `_classify_do_failure` as new ones show up.
9. ✅ **Cost estimate** — base DONE this session (`/v2/apps/propose` → DO's own
   number → `estimated_monthly_usd` → header pill + "Est. cost" Row). Shows DO's
   figure as-is. Still-open polish (later, not blocking):
   - **Pre-deploy pill** (before committing money): needs the plan, which today
     is only built at submit. Option: a lightweight estimate endpoint that
     plans + proposes, or reuse the prior version's spec on a redeploy.
   - **Fallback** to `GET /v2/apps/tiers/instance_sizes` prices if `propose`
     ever returns no cost (today: show nothing — honest but blank).
   - Confirm DO's cost field name against a real response (`_COST_KEYS`).

10. **UX clarity pass** — make the whole deploy flow obviously simple and not
    overloaded: the stepper, planner picker (manual-key behind disclosure),
    private-repo help, error states. Goal: a non-dev can get through it
    without head-scratching. Audit for noise / hidden state / dead ends.

11. ✅ **Full log trace in Ship** (build + deploy + runtime) — DONE (viewer).
    New "Logs" tab in the AppCard fetches DO's BUILD/DEPLOY/RUN streams for the
    current deployment, server-side (`services/deploy/logs.py` →
    `do.deployment_logs`; presigned BUILD/DEPLOY archives + RUN proxy snapshot),
    ANSI-stripped and tailed to 200k chars. Endpoint
    `GET /workspaces/{ws}/deployments/{id}/logs?type=BUILD|DEPLOY|RUN`; Next
    proxy `/api/deployment/logs`. Verified against live monorepotest logs.
    **Still open (idea, confirm scope):** pipe a *failed* deploy's logs to a
    **Ship agent that diagnoses + opens a fix PR** (on-brand). [TBD with vvlad.]

12. **Test fixtures** (`deploy-test-fixtures/`, DONE this session) — three
    deliberately janky-but-working projects to stress the planner. vvlad lifts
    each into its own GitHub repo, then deploys via Ship:
    - `monorepo-janky/` — Express api (`api/`, binds 127.0.0.1, health `/healthz`,
      Dockerfile at `api/Dockerfile`) + Vite/React `dashboard/` (hardcodes
      localhost under the odd name `VITE_BACKEND_BASE`); root `package.json`
      workspaces (the "is root the app?" trap). Tests split + dockerfile path +
      HOST + name-agnostic `$APP_URL` wiring + non-`/health` path.
    - `backend-only-janky/` — FastAPI (different stack), uvicorn defaults to
      127.0.0.1. Tests single-service detection + HOST injection.
    - `frontend-only-janky/` — vanilla Vite; backend URL under a misspelled
      `VITE_API_RUL`. Tests single static_site + the lone-frontend "where's the
      backend URL?" question + meaning-based env detection.
    Every frontend has a **"Check backend health" button** → proves front↔back
    wiring after deploy. Each folder has its own README (verify checklist +
    local-run). `node_modules`/`dist` not included (run `npm install`).
    Principle followed (vvlad): *don't cheat — be like a human, make it working
    but janky*, so the test is meaningful.

### Ideas to run by vvlad (need his go/no-go)
- **Logs → agent auto-fix loop**: deploy fails → Ship captures DO logs →
  translates the failure to plain language (like we did for 403/422/health) →
  offers "let a Ship agent diagnose & open a fix PR". [confirm]
- **One-click recovery on a failed deploy**: "re-plan with a stronger model"
  and "fix & redeploy" buttons right on the failed AppCard. [confirm]
- **Plan/spec diff between deploy versions** (pairs with deploy versioning) so
  you see what changed before a rollback. [confirm]
- **"Why did it cost change?"** note when run-rate changes between versions
  (e.g. instance size bumped). [confirm]

## Notes / gotchas
- Backend isn't volume-mounted in docker-compose → after backend edits:
  `docker compose build ship-server && docker compose up -d ship-server`.
- Two planning paths: **manual key** → synchronous backend planning (current);
  **no key** → dispatch `ship-deploy-plan.yml` in GitHub Actions (needs the
  Actions permission + the workflow registered on the repo).
- `*.ondigitalocean.app` DNS can lag locally (negative cache on router/ISP);
  use 1.1.1.1/8.8.8.8 or wait — not a deploy problem.
