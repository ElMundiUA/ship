# Deploy feature — phased plan & progress

One-click deploy of a workspace's repo to a cloud provider. First provider:
**DigitalOcean App Platform**. Architecture: a module inside `apps/backend`
(`app/services/deploy/`) + a console surface, designed for clean later
extraction. Philosophy (from product owner): **minimal user load — one
button; the AI analyses and deploys, and only stops the user at genuine
gates (connect provider / repo access / required secrets).** Users see
"works or not" + a healthcheck, not internal specs.

Operator setup lives in [DEPLOY_SETUP.md](DEPLOY_SETUP.md).

---

## ✅ MILESTONE — first real deploy is GREEN (2026-06-01)

The full one-click flow works end to end and produced a live app:
**user clicked Deploy → Gemini planned the repo → DO App Platform built &
deployed → live URL** `https://wonnabe-frontend-ujfsr.ondigitalocean.app`
(public `WonnaBeCodeFather/frontend`, deployed as a static React SPA).

DO is **Connected** (OAuth done). Backend healthy. The MVP backbone
(Connect → Plan → Execute → Track) is proven. **Next focus per product owner:
UX polish** (see "UX backlog" below) + the private-repo auth gate (Phase 9).

### Environment / infra facts (so the next session doesn't rediscover them)
- Backend runs in **docker** (`ship-ship-server-1`, port 8100). Console runs
  **locally** (`make dev-console`, port 3001) — NOT in docker.
- After backend code/env changes: `docker compose up -d --build ship-server`.
  Migrations are already applied (alembic head `0082_deployments_table`).
- DO OAuth app is registered; **Client ID/Secret are in root `.env`** and are
  now passed into the container via `docker-compose.yml` `environment:` (they
  were missing before — the `ship-server` env list is explicit, not env_file).
- Console→backend calls from the browser must go through **dedicated Next
  route handlers** under `apps/console/src/app/api/*` (they attach the session
  bearer via `getSessionToken` + `@/lib/api/client`). Do NOT use `/api/proxy/*`
  for authed calls — a `next.config` rewrite `/api/:path* → backend/v1/:path*`
  shadows it and drops the bearer. New handlers added: `api/repos`,
  `api/deployments`, `api/deployments/[id]`, `api/deploy`, `api/deploy/providers`,
  `api/deploy/connect`.

### Phase 7 progress (live test 2026-06-01)
- DO **Connected** via OAuth ✅ (modal shows green "Connected").
- First real Deploy clicked → row created → **Failed** at planning with a clean
  message: "No LLM configured for the deploy planner…". This is expected — the
  backend has no OPENAI/ANTHROPIC key and the Gemini dev fallback is off.
- **Next:** wire an LLM key (user's Gemini via `DEPLOY_PLANNER_GEMINI_API_KEY`
  + `DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true`, then rebuild), then redeploy.
- The test repo (`frontend`) is **private** → after the LLM works, expect the
  DO step to fail until DO↔GitHub is authorized (Phase 9). Use a **public**
  Streamlit repo for the first fully-green deploy.

### Phase 7 — second live test (Gemini wired, 2026-06-01)
The full pipeline now runs; we walked the error chain to the real-world gate:
1. ✅ Gemini planner works — produced plan "Frontend application deployed as a
   static site built with Node.js" (`gemini-3.5-flash`, dev fallback).
2. ✅ DO API actually called with the OAuth token.
3. Fixed: spec put `source_dir` inside the github/git source — DO rejected
   (`unknown field source_dir in GitHubSourceSpec`). `source_dir` is a
   COMPONENT-level field; moved it out in all 3 builders.
4. Hit the `GitHub user not authenticated` gate (private repo) → made the repo
   public for the test; adapter used the `git` source → **DO accepted, built,
   ACTIVE**. Green. (Making a repo public is a TEST shortcut, NOT a product
   flow — real private repos need Phase 9.)

### More bugs found & fixed (round 2)
- Single-deployment poll `/api/deployments/[id]` returned 404: a **dynamic**
  Next route is shadowed by the `afterFiles` rewrite `/api/:path*` (rewrite
  runs after static routes but BEFORE dynamic ones). Fixed by using a STATIC
  route `/api/deployment?ws=&id=` instead. **Rule: any new console→backend call
  must be a static `/api/*` route, never a `[param]` dynamic one.**
- Healthcheck probed `/health` (404 on a static SPA) → red dot on a working
  site. Default changed to `/`. Needs backend rebuild to take effect; the
  first app's dot stays red until a redeploy (active deploys aren't re-polled).

## UX backlog — NEXT FOCUS (product owner pivot 2026-06-01)
Friction observed while driving the real flow as a user. None block deploy;
all make it nicer / more honest for a simple user:
- [x] **App cards (DONE 2026-06-01).** Page now groups deployment rows by
      **app = (repo_id, provider)** instead of a flat list of attempts. Each
      app is an expandable card: collapsed = status + URL + health + Redeploy;
      expanded = Overview / History / Logs(placeholder) / Settings(placeholder).
      Multiple apps per workspace (frontend + backend) render as separate cards
      — exactly the multi-deploy case. History consolidates all attempts.
- [x] **Healthcheck fixed + on-demand re-check (DONE).** Default probe is `/`
      (was `/health`, 404 on SPAs); single-GET re-checks health for ACTIVE
      apps so the "re-check" button flips the dot green when the site answers.
- [ ] **Live status without manual refresh polish.** Modal closes on deploy and
      the row streams status — confirm the auto-poll feels smooth; show a
      spinner/phase text ("Building on DigitalOcean…").
- [x] **One DO app per (repo,provider): redeploy UPDATES in place (DONE 2026-06-01).**
      The deploy route now finds the newest prior deployment's `provider_ref.app_id`
      for this repo+provider and passes it to the adapter, which calls
      `PUT /v2/apps/{id}` (update) instead of `POST /v2/apps` (create). Verified:
      after a Redeploy the new row carries the SAME app_id and DB shows exactly
      **1 distinct DO app** across all attempts — no orphans. `update_app` added
      to the DO client; `apply(..., existing_app_id=...)` on the provider.
- [ ] **Errors are developer-speak.** Raw DO/LLM messages (e.g. "No LLM
      configured", "GitHub user not authenticated", DO 400 JSON) must never
      reach a simple user. Map known errors to friendly copy + an action.
- [ ] **Private-repo gate as UX (ties to Phase 9):** detect "GitHub user not
      authenticated" → show "DigitalOcean needs access to this repo →
      [Authorize]" (links to `github.com/apps/digitalocean/installations/new`),
      not a red error. One-time per GitHub account.
- [x] **Periodic health monitor (DONE 2026-06-01).** Backend cron
      `deployments_health` (APScheduler + advisory lock, `CronLockId
      .DEPLOYMENTS_HEALTH=1023`) re-probes every ACTIVE deployment's live URL
      **every 15 min** and updates `healthy` — works even with the console
      closed. Shared `services/deploy/health.py` (`health_check_path`, `probe`,
      `recheck_active_deployments`); the deploy route's on-demand re-check now
      reuses it too. Verified: recheck ran (urls_checked=1), scheduler started
      clean. Optional follow-up: light frontend auto-refresh (e.g. 60s) so an
      OPEN page reflects cron updates without manual re-check.
- [ ] **Deploy progress detail** (optional): a compact "Analyzing → Building →
      Live" stepper in the row, still honoring "users only need works/not".
- [ ] **Empty/connected states**: when DO is connected, the modal could skip
      straight to repo + Deploy; show the connected DO account name.

### Bugs found & fixed this session (don't reintroduce)
- Migration `0082` created `deployments.id` without `server_default
  gen_random_uuid()` → INSERT NOT NULL violation. Fixed in `0082` + added
  `0083` to backfill the default on the existing DB.
- Modal rendered the backend error envelope object `{code, error_class,
  message, path}` directly as a React child → crash. Added `errText()` to
  coerce any error body to a string.
- Browser called `/api/proxy/v1/...` → 404 (rewrite doubled `/v1`, no auth).
  Fixed by switching to dedicated route handlers.
- `ship-server` `environment:` lacked DigitalOcean + planner vars → 503
  "DigitalOcean OAuth is not configured". Added them to `docker-compose.yml`.
- `DEPLOY_PLANNER_ALLOW_DEV_FALLBACK` is a `bool`; compose default `:-}` (empty
  string) crashed pydantic Settings on boot. Fixed default to `:-false`.

### Known caveat for the first test
- The only connected repo (`WonnaBeCodeFather/frontend`) is **private** → DO
  needs its GitHub app authorized (Phase 9). For the **first** clean test use a
  **public** repo (Streamlit) so the `git` source works with zero extra auth.

---

## Phase 1 — DigitalOcean OAuth connect (backend) ✅ DONE
- [x] `digitalocean` native-integration provider (enum + DB check constraint, migration `0081`)
- [x] env config (`DIGITALOCEAN_CLIENT_ID/SECRET`, scopes, refresh hours)
- [x] OAuth helpers (state mint/verify, authorize URL, code exchange, refresh) — `integrations/digitalocean/oauth.py`
- [x] `install/start` + public `install/callback` routes; access+refresh tokens stored encrypted (Fernet) + expiry + audit
- [x] Router registration

## Phase 2 — LLM deploy planner ✅ DONE
- [x] Provider-agnostic `DeployPlan` IR — `services/deploy/plan.py`
- [x] Planner: repo_intel + key files → structured LLM call → validated `DeployPlan` — `services/deploy/planner.py`
- [x] LLM resolver: Ship vendor (OpenAI/Anthropic) + gated local-dev Gemini fallback — `services/deploy/llm.py`

## Phase 3 — DigitalOcean App Platform executor ✅ DONE
- [x] DO REST client (create app, get app, deployments, phase helpers) — `integrations/digitalocean/client.py`
- [x] `DeployProvider` protocol + `ProviderRef`/`DeploymentStatus` — `services/deploy/providers/base.py`
- [x] DO adapter: `DeployPlan` → app spec (public=git / private=github), apply/status/health — `services/deploy/providers/digitalocean.py`
- [x] Credential resolver (decrypt DO token) — `services/deploy/credentials.py`

## Phase 4 — Deployments table + routes + initial console UI ✅ DONE
- [x] `Deployment` model + migration `0082`
- [x] `POST /workspaces/{ws}/repos/{id}/deploy` (plan→execute), `GET /workspaces/{ws}/deployments/{id}` (lazy DO poll), list endpoints
- [x] Console API client fns + initial Deployments surface
- [x] DO OAuth app registered; creds in `.env`; migrations applied

## Phase 5 — Consolidate console UI into ONE Deployments page ✅ DONE
Decision: everything on the workspace **Deployments** page (no spreading).
A single **modal** ("New deployment") does repo + provider + deploy — NOT a
multi-step wizard. Live monitoring list below.
- [x] Backend: `repo_full_name` in deployment output; `GET /workspaces/{ws}/deploy/providers` (connected status)
- [x] Console client fns: `listDeployProviders`, `startDigitalOceanConnect`
- [x] Deployments page: client component with monitoring list (polling in-flight)
- [x] "New deployment" modal: repo picker + provider (DO) + Deploy
- [x] Removed per-repo Deployments tab + repo-nav item (consolidated)

## Phase 6 — Connect-DO gate (inline) ✅ DONE
- [x] Modal shows "Connect DigitalOcean" when provider not connected → starts OAuth → redirects to DO → back to console
- [x] When connected, modal shows the Deploy button; 409 on deploy flips the gate back to Connect

## Phase 7 — First real deploy end-to-end ✅ DONE
Achieved 2026-06-01: public `WonnaBeCodeFather/frontend` → ACTIVE + live URL.
Walked the full error chain to green; all fixes below are in. Remaining nit:
healthcheck for the first app shows red because it was computed once (at
ACTIVE) against `/health`; fixed the default to `/` for static sites — a
redeploy (or re-poll of active deploys) will show green. Original checklist:
Concrete steps for the next session:
- [ ] In console → Deployments → New deployment → **Connect DigitalOcean**;
      finish DO consent; callback returns to `/integrations` and the
      `native_integration_installations` row + tokens get written.
      (If the modal still shows "Not connected" after, refresh — providers
      endpoint should now report `connected: true`.)
- [ ] Activate a **public** Streamlit repo in the workspace (or make the test
      repo public) so the `git` source path needs no DO↔GitHub auth.
- [ ] Click **Deploy**; expect: row appears `Analyzing… → Deploying (BUILDING/
      DEPLOYING) → Active`, then a live URL + green healthcheck dot.
- [ ] Open the live URL; confirm the Streamlit app serves.
- [ ] If the planner has no LLM key in the container: set `OPENAI_API_KEY`/
      `ANTHROPIC_API_KEY` in `.env` (then rebuild), or enable the gated Gemini
      dev fallback (`DEPLOY_PLANNER_GEMINI_API_KEY` + `DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true`).
- [ ] Likely first-deploy fixes to expect: app-name slug rules (2–32, lowercase),
      `instance_size_slug` validity, health-check timing for Streamlit cold start.
- [ ] Remove leftover screenshot PNGs in repo root created during testing.

## Phase L — Lifecycle: Stop / Delete (real teardown)  ✅ DONE
Problem: a deployed DO app keeps running (and billing) until explicitly
removed. App Platform has **no pause** — deleting the app is the only way to
stop it. Right now a dummy app is live with no UI to remove it.
Best practices (bake in):
- **Delete** = `DELETE /v2/apps/{id}` (building block `client.delete_app` added)
  → stops billing + keeps the card/history as `deleted`. **Stop** (optional) =
  same DO delete but keep the row as `stopped` so "turn back on" = redeploy.
- **Destructive confirm** (two-step button or type-the-name) — irreversible.
- **Idempotent**: treat DO 404 as "already gone"; DO is the source of truth,
  our rows are a cache.
- **Soft-delete for audit**: `deleted_at` + status, keep provider handles.
- **RBAC**: admin only.
- ⚠️ **Orphan-billing risk (HIGH):** deactivating a repo / deleting a workspace
  cascades our rows but leaves the DO app running and billing. Hook teardown
  into repo-deactivation + workspace-deletion so we never strand a paid app.
- **Reconcile cron**: periodically diff our deployments vs DO's real apps
  (app deleted in DO dashboard → mark ours; surface orphan DO apps).
Build order: (1) per-app Delete button (Settings tab) + `delete_app` route,
(2) orphan-billing hooks, (3) reconcile cron.

**(1) DONE 2026-06-01 — verified live.** `client.delete_app` (DELETE /v2/apps/{id});
backend `DELETE /v1/workspaces/{ws}/repos/{repo_id}/deploy` (admin, idempotent on
DO 404, soft-deletes rows only after DO delete succeeds so we never orphan a
billing app and never lose the provider `app_id`); console static route
`/api/deploy/teardown` + a two-step confirm "Delete" button in the card
Settings tab. Verified: deleted the live app → DO API returns 404 for the app
id (really gone, billing stopped); current behavior keeps DB rows as `deleted`.
**(2) DONE 2026-06-01 — orphan-billing hooks.** Shared `services/deploy/
teardown.py` (`teardown_repo_app`, `teardown_workspace_apps`, idempotent on DO
404, soft-deletes rows after the DO delete succeeds). Wired BEFORE the cascade
in: **workspace delete** (`workspaces.delete_workspace`) and **repo disconnect**
(`repos.disconnect_repo`). These hooks now block deletion/disconnect if DO
teardown cannot be confirmed, so provider handles are not lost while an app may
still be billing. The deploy DELETE route reuses `teardown_repo_app` too.
Provider-specific lifecycle calls are dispatched through
`services/deploy/providers/operations.py`; DO rollback/delete REST details stay
inside the DigitalOcean adapter/client, not in route handlers.

**(3) DONE 2026-06-01 — reconcile cron + activity feed (verified live).**
- `services/deploy/reconcile.py` + cron `deployments_reconcile`
  (`CronLockId.DEPLOYMENTS_RECONCILE=1024`, every 30 min): for each ACTIVE
  DO deployment, checks the app still exists; on 404 (removed outside Ship)
  flips the row to **failed** (red), clears live_url/health, and records an
  activity event. Only ever queries app_ids we recorded — never touches the
  user's other DO apps.
- **Activity feed** (the "read what happened" ask): new `deployment_events`
  table (migration `0084`, keyed per app), `services/deploy/events.py`
  (`record_event`/`list_app_events`), events written on **deployed** /
  **deploy_failed** / **removed_externally**. Surfaced as the card's
  **Activity** tab (replaced the Logs placeholder). API:
  `GET /workspaces/{ws}/repos/{id}/deploy/events` + console `/api/deploy/events`.
- **Verified live**: deployed → "deployed" event; externally deleted the DO
  app → reconcile flipped the card to red "Removed on DigitalOcean" + wrote
  the event; Activity tab shows the full story; Redeploy available to restore.

Phase L (lifecycle/billing) is COMPLETE: explicit Delete, orphan-billing
hooks, and drift reconcile all done & verified.

## Phase V — Versions (numbered, commit-pinned + 1-click rollback)
**Base versioning and DigitalOcean provider rollback are implemented.**
- [x] **Human version number per app**: every deploy is shown as `v1, v2,
  v3…` for that (repo, provider). The console's History tab is now
  **Versions** with status · time · cost · plan summary · current marker.
- [x] **Rebuild from a previous version's saved plan**: `Rebuild plan` on an old
  version calls `POST /deployments/{id}/redeploy`, reuses that version's stored
  `DeployPlan` with no LLM re-plan, and applies it to the same DO app. New rows
  carry `redeployed_from_id`; UI renders `rebuilt from vN` so users understand
  that v6 may be a rebuild of v1's plan.
- [x] **True DigitalOcean rollback**: `Rollback` on a successful old version
  calls `POST /deployments/{id}/rollback`, validates with
  `/v2/apps/{app_id}/rollback/validate`, then rolls back using
  `/v2/apps/{app_id}/rollback` to that version's DO `deployment_id`
  (`skip_pin: true`). New rows carry `rolled_back_from_id`; UI renders
  `current · rollback to vN`.
- [ ] **Commit-pinned versions**: record `commit_sha` + branch + message +
  author + time at deploy time (fetch branch HEAD via CodeHostGateway).
- [ ] **Per-version build logs** tied to immutable versions.

Likely migration: add commit/source metadata to `deployments` (or a
`source`/`config` JSONB) and keep provider deployment ids per version.

## Phase 8 — Secrets gate
- [ ] When the plan declares required/secret env vars, collect values before deploy
- [ ] Pass secrets into the DO app spec securely (never persisted in plaintext)

## Phase 9 — Private repo path (DO ↔ GitHub authorization)
- [ ] Detect private repo; surface "Authorize DigitalOcean GitHub access" gate
- [ ] Deploy via `github` source once authorized

## Phase 10 — DO token refresh cron (30-day expiry)
- [ ] Background tick rotates access+refresh pair before expiry (mirror `linear_token_refresh`)

## Phase 11 — Polish (post-MVP)
- [ ] Friendly error messages; redeploy; delete/teardown app
- [ ] UI/UX refinement once the flow is proven

## Multi-provider readiness (DESIGN NOTE — already in place)
DigitalOcean is the first provider; **AWS, Azure, etc. are planned**. The
design is already provider-agnostic so adding a provider = one new adapter,
no rewrites:
- `DeployProvider` protocol (`services/deploy/providers/base.py`) — add `providers/aws.py`, `providers/azure.py`, …
- `DeployPlan` IR is provider-neutral; the LLM plans once, each adapter maps it.
- `Deployment.provider` column + `ProviderRef.provider` already carry the choice.
- `GET /deploy/providers` returns a LIST; add an entry + connect path per provider.
- The "New deployment" modal renders Provider as a selectable (one card today).
Adding a provider later: new adapter + new OAuth/credential connect + one
providers-list entry. No changes to the planner or the deployments table.
