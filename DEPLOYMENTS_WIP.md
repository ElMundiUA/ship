# Deployments — WIP / handoff notes

Branch: `vv-deployments`. Working notes for the DigitalOcean deploy feature +
deploy planner. Update this as we go (this is the "where are we" doc).

## Current focus
Deploy **planner robustness** — turning "any repo" into a correct DO App
Platform deploy without the operator hand-fixing things.

## ⏱ WHERE WE ARE — 2026-06-03 (latest; read this first)
Live dogfooding the deploy flow with vvlad. Backend rebuilt + tsc clean throughout.

**Local commits ahead of `origin/vv-deployments` (NOT pushed — push HELD on
vvlad's call until the whole feature is ready; also git creds were on
`WonnaBeCodeFather` which 403s on `ElMundiUA/ship`, so vvlad must re-auth before
push):**
- `571dfdcb` fix: manual LLM key wrongly blocked at Deploy (guard checked repo
  GitHub-secret keys even in manual mode → "Add an LLM API key…"; now mirrors
  plannerReady). + clear error on step nav.
- `00dc1c31` fix: connect returns to the wizard's DigitalOcean step (returnPath
  deep-link `?deployConnect=<repo>`), not the Integrations page; popup-block →
  same-tab fallback.
- `8c02b6fc` feat: deploy versioning — History tab → **Versions** (v#, status,
  time, cost, plan summary, "current"); **rollback** via
  `POST /deployments/{id}/redeploy` (reuses a version's stored plan verbatim,
  same DO app). **Live rollback still needs a real test by vvlad.**
- `5c82766e` feat: dummy-proof wizard — why-blocked hint under disabled
  Next/Deploy; "what happens next" microcopy; hide private-repo box when the
  repo already has a live DO app.
- `fcf34ae8` feat: "View logs" link from a failed deploy's error.
(Already PUSHED earlier: `1f638106` logs viewer, plus the 4 base commits.)

## 🤖 Autonomous solo run (vvlad away, approved) — local commits, NO push
Order: (1) reactive authorize-as-step ✅ → (2) plan/spec diff between versions →
(3) per-version logs → (4) commit-pinned versions → (5) cost instance-size
fallback. Each: tsc/unit + local commit + this doc updated. NO live DO deploys,
NO push (held on vvlad git creds: `WonnaBeCodeFather` 403s `ElMundiUA/ship`).

Checkpointed first: `61e89af0` (Codex native-rollback/normalization/lifecycle
WIP) + `37002f6c` (remove test fixtures → standalone repos).

- ✅ **(1) Reactive authorize-as-step** — DONE `fb164aba`. Removed the proactive
  yellow "needs access" box (can't pre-check DO access). On
  `error_kind=github_access` the card shows a calm blue "One more step — let
  DigitalOcean read this repo" panel (Authorize DigitalOcean → / make public →
  Redeploy); the red error row is suppressed for that kind so it reads as a
  step, not a failure. (PrivateRepoHelp lost its variant; only reactive now.)
- ✅ **(2) plan/spec diff between versions** — DONE. Each prior version in the
  Versions tab has a **`diff`** toggle showing what changed vs current
  (components added/removed; per-component runtime/port/source_dir/dockerfile/
  health/routes/env changes; secrets shown as •••). Pure client-side over
  `plan_components` (`planDiffLines`). Read-only.
- ✅ **(3) per-version logs** — DONE. Each version row has a **`logs`** link
  that points the Logs tab at THAT version's deployment (BUILD/DEPLOY/RUN);
  a "view current →" reset returns to the live one. (`logsDeploymentId` state.)
- ✅ **(4) commit-pinned versions** — DONE `274d3f1a`. Manual deploy now fetches
  branch HEAD via `GitHubCodeHost.get_branch_commit` (sha/msg/author/date),
  stores it in `provider_ref.commit` (no migration), exposes
  `commit_*` on `ApiDeploymentOut`, Versions row shows short-sha + message.
  **Follow-up:** capture commit on the Actions-planner path (plan-result callback);
  native-rollback version could copy the rolled-back-to version's commit.
- ✅ **(5) cost fallback** — DONE `3b2da0fc`. `client.instance_sizes` +
  `instance_price_map` + adapter `_estimate_spec_cost`; used when
  `propose_monthly_cost` is None. Still DO's own published prices.

### Live dogfood fix (2026-06-04) — stale-commit deploy failures
Found during the rollback test: vvlad force-pushed `monorepo-janky`; DO's GitHub
integration cached the old branch tip and kept trying a rewritten commit
(`error checking out commit: object not found`) across redeploys — a plain
Redeploy is USELESS (DO holds the stale tip; only a fresh push refreshes it).
Fix `04d466e6`: on a failed deploy, scan the BUILD log → if it's a stale-commit
checkout, surface `git_ref_stale` ("push a NEW commit, not --force, then
redeploy"). Also replaced the useless `app spec updated` error with DO's
structured progress reason. Verified against the real stuck deployment.
**Lesson for the deploy branch: don't force-push it** (corrupts DO's cached ref
+ breaks shallow clones).

### Deeper root cause + fix (2026-06-04) — VALIDATED LIVE
The stale commit wasn't just force-push: **`update_app` (PUT /v2/apps/{id}) does
NOT re-pull source** — DO reuses its cached branch tip, so Ship-triggered
redeploys kept building the dead commit (confirmed by DO community thread
"app-checking-out-wrong-commit-during-build"). Fix `a582809b`: on redeploy of an
existing app, after the PUT we call `create_deployment(force_build=true)`
(`POST /v2/apps/{id}/deployments`) which DO documents as pulling the latest
commit. **Validated live:** after the fix DO built the real HEAD `d3bf8d70`
(was stuck on `8439544c` across 3 redeploys), went Active.

### Rollback test — PASSED (2026-06-04)
Native DO rollback completes, serves the correct prior artifact, and the
rolled-back app renders + its healthcheck button hits the backend (`{"ok":true}`).
A force-push does NOT block rollback (rollback restores DO's stored build
artifact, not a git fetch). Note: a brief blank/white screen during a rollback
is DO swapping the live deployment — a tab loaded in that window shows white
until refresh (DO behavior, not a Ship bug). The port-change "break" was inert
(planner injects `VITE_BACKEND_BASE=$APP_URL/api`, overriding the localhost
fallback — the planner doing its job), so the broken→fixed narrative wasn't
exercised; rollback machinery itself is proven.

**Autonomous run COMPLETE — all 5 solo items done (steps 1-5).** 14 local commits
ahead of `origin/vv-deployments`, push still HELD (vvlad git re-auth needed:
`WonnaBeCodeFather` 403s `ElMundiUA/ship`). All tsc clean, backend rebuilt +
healthy, Codex's 10 tests still green.

**Needs vvlad:** live deploy/rollback/teardown tests · push (git re-auth) ·
Actions re-seed for old repos (so the workflow emits `connections`) · secrets-gate design.

**Confirmed live today:** frontend↔backend connectivity works on the janky
monorepo after route-prefix normalization (`VITE_BACKEND_BASE=$APP_URL/api`).
The old failure was frontend calling `/healthz` on the root static site and
getting HTML. Health-probe path fixed too (`/api/healthz` / `/api/health` style
external path, DO strips the route prefix internally). Actions-parity is now
partially covered by the shared submit-time normalization; the workflow prompt
was also updated to emit `connections`, but already-seeded repos need re-seed
before the workflow itself learns the new schema/prompt.

## ✅ Done this autonomous session — NEEDS VVLAD REVIEW (hand-test, NOT committed)
All of the below is in the working tree on `vv-deployments`, **uncommitted**.
Backend was rebuilt (`docker compose up -d --build ship-server`) and
unit-checked; console `next build` passes with existing warnings. The *live*
visual + real-DO confirmations are intentionally left for vvlad where noted
(see "Phase 5" below).

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
5. **Planner connectivity contract + DO route-prefix normalization**
   (`services/deploy/plan.py`, `planner.py`, `deploy.py`,
   `ship-deploy-plan.yml`). `DeployPlan` now has top-level `connections`
   (`from_component`, `to_component`, `env_key`, `public_base_path`, `value`,
   `preserve_path_prefix`) so frontend→backend wiring is explicit instead of
   inferred only from env names. The planner prompt explicitly says repos
   normally do **not** contain `.env`; source code is authoritative and
   `.env.example` is only a hint. Before saving/submitting to DO, Ship
   normalizes frontend API-base envs using `connections` or a deterministic
   static-site→service fallback: `$APP_URL` + backend public route (e.g.
   `$APP_URL/api`). This covers both manual planner and Actions planner paths.
6. **Raw plan visibility** (`deploy.py` `plan_debug` + Console `Plan` tab).
   Each deployment card now exposes the stored DeployPlan JSON with secret env
   values masked, so operators can verify `connections`, routes, env values,
   and health paths without guessing from provider behavior.
7. **Version recovery paths are split clearly.** `Rollback` now uses
   DigitalOcean's native app rollback API against the target version's stored
   provider `deployment_id` and creates a new Ship version marked
   `rollback to vN`. `Rebuild plan` keeps the older deterministic behavior:
   reuse the saved DeployPlan and build again on the same DO app.
8. **Billing-safe delete semantics.** Explicit Delete now soft-deletes
   deployment rows (`status=deleted`, provider `app_id` retained) only after
   DigitalOcean app delete succeeds. Repo disconnect / workspace delete now
   block if DO teardown cannot be confirmed, so Ship does not cascade away the
   rows/token needed to stop a still-billing app.
9. **Provider lifecycle boundary.** Native rollback/delete/credential dispatch
   now lives under `services/deploy/providers/` (`operations.py`,
   `capabilities.py`, provider adapter methods). Routes and generic teardown no
   longer call DO rollback/delete REST helpers directly.

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

## ✅ Actions-planner parity — DONE (2026-06-04, commit `d64597e8`)
`ship-deploy-plan.yml` was brought to parity with the backend planner
(`services/deploy/planner.py`) so the no-manual-key (Actions) path plans as well
as the manual path. Verified **statically** (can't run without a real no-key
deploy):
- **System prompt byte-for-byte identical** to `planner._SYSTEM_PROMPT` (diffed:
  3511 bytes both). The planner's "brain" is the same.
- Full **file tree + manifest-layout map + directory-diverse key-file
  collection** (expanded basenames incl. frontend entries), 24/3k/48k — mirrors
  `_collect_key_files`. The workflow reads the checked-out FS (more accurate than
  the backend's API file list).
- **max_tokens 8192** (was 2048 → truncated big monorepos).
- **verify-guard + 3-attempt corrective retry** mirroring `_verify_plan`
  (dockerfile-exists, source_dir-exists, loopback-not-wired, route-prefix).
- Model defaults already match `PROVIDER_DEFAULT_MODEL`.
- Plan is **Pydantic-validated** at `/plan-result` (`DeployPlanResultIn.plan:
  DeployPlan`), and route normalization / HOST / dockerfile / create_deployment /
  cost all run on the returned plan at submit — **shared by both paths**.
- Bumped `BUNDLE_VERSION` 0.39 → **0.40**.

**Remaining deltas (honest):** (a) no backend `RepoIntel` signals in the Actions
prompt — the full file tree + key files compensate (minor); (b) **commit-pin not
yet on the Actions path** (the plan-result callback doesn't fetch the HEAD commit
— follow-up). **Still needs:** a real **no-key deploy to live-verify**, and a
**re-seed of repos** (push `ship-deploy-plan.yml` v0.40 to default branches) so
the Actions path uses the new planner. KEEP THE TWO IN SYNC: change `planner.py`
→ change the workflow → bump `BUNDLE_VERSION` → re-seed.

## DECISION (2026-06-04, vvlad) — keep the planner clean; wiring is config
A standalone frontend deployed alone (e.g. `frontend-only-janky`) had its API-base
env wired to `$APP_URL` (its OWN url), so its healthcheck button hit `<self>/health`
→ 404; the real backend is a SEPARATE app. This is NOT a planner bug — the planner
faithfully translated the repo (correctly detected the API-base env by meaning).
Which separate deployment is "the backend" is operator topology, not in the repo.
**DECISION: do NOT add lone-frontend heuristics to the planner** (no special-casing,
no extra determinism). Cross-app backend URLs are solved at the code/config level —
i.e. the operator sets the env. The real lever is **env editing in Ship (Phase 8)**,
not planner changes. Planner stays as the clean "translate the repo" component.

## 🔜 Phase 8 — env / secrets gate (NEXT FEATURE — designed, not built)

### The problem
The planner declares env var NAMES; `_build_envs` (DO adapter) puts them in the
spec as `type: SECRET`/`GENERAL`, `scope: RUN_AND_BUILD_TIME`, but **secrets are
sent with `value: ""`** — Ship never collects the actual values. So:
- an app that needs a real secret (DB password, API key) deploys without it →
  runtime failure;
- cross-app config (a standalone frontend's backend URL, e.g. `VITE_API_RUL`)
  can't be set → wrong wiring (the lone-frontend 404 we hit);
- **worse — a latent data-loss bug TODAY:** on redeploy Ship does
  `update_app(spec)` with `value: ""` for secrets, which **WIPES** any secret the
  operator set on DO. Must be fixed regardless of the feature.

### What we know about DO env/secrets (researched 2026-06-04, with sources)
- **SECRET** is stored encrypted by DO as `EV[1:...]` ciphertext — **write-only**
  (can't read the plaintext back). ([env how-to](https://docs.digitalocean.com/products/app-platform/how-to/use-environment-variables/), [app-spec ref](https://docs.digitalocean.com/products/app-platform/reference/app-spec/))
- On **CREATE** a secret must be **plaintext** (DO encrypts it); error otherwise
  ("secret env value must not be encrypted before app is created"). ([thread](https://www.digitalocean.com/community/questions/error-with-dolt-secret-env-value-must-not-be-encrypted-before-app-is-created))
- On **UPDATE** you MUST resend the **encrypted** value (`EV[1:...]`) for unchanged
  secrets, or DO **wipes** them — widely reported footgun ([env removed on each
  update](https://www.digitalocean.com/community/questions/environmental-variables-are-getting-removed-on-each-app-update), [doctl drops global envs #934](https://github.com/digitalocean/doctl/issues/934)).
  Best practice: export the spec (with encrypted secrets) and reuse it for
  updates ([best-practices thread](https://www.digitalocean.com/community/questions/what-are-app-spec-best-practices-for-keeping-env-secrets-secret)).
- **Dashboard:** Settings tab → pick a component → Environment Variables → Edit.
  Deep link: `https://cloud.digitalocean.com/apps/{app_id}/settings`.
- **Bindable vars** (useful for #4 internal wiring): `${APP_URL}`, `${APP_DOMAIN}`,
  `${_self.PRIVATE_DOMAIN}` (service↔service), `${<db-name>.DATABASE_URL}`.

### Chosen solution (simplest safe design vvlad + Claude converged on)
- **UI — env fields live in the Deploy STEP (per repo/app), not a separate early
  step** (vvlad: env is intrinsically per-repo; a real product is often 2 repos =
  2 apps = 2 env sets). Show the plan's env contract for that repo's components:
  non-secret config (HOST, `$APP_URL`, `VITE_API_RUL`) prefilled; required/secret
  → input fields; on **redeploy** show already-set secrets as **"•••set"** (read
  from DO) with an option to change. Same editor on the card's **Settings** tab
  for later edits.
- **Backend — preserve-on-update (MANDATORY, also fixes the data-loss bug):**
  before `update_app`, GET the current app spec and **merge back the encrypted
  `EV[1:...]`** for every SECRET the operator already set; send only NEW/changed
  values as plaintext. Never send `value: ""` for an existing secret. (Native
  rollback already restores the prior artifact's secrets — DO handles it.)
- **Ship does NOT persist secret values** — they're transient in the deploy
  request (exactly like the manual LLM key today); DO holds them encrypted →
  lower liability. ("Responsibility on DO.")
- **Multi-repo / cross-app:** each repo deploys to its own app with its own env
  set in its own Deploy step. The frontend's backend URL is a plain GENERAL env
  the operator sets in the frontend's Deploy step (the operator-level lone-frontend
  fix from the DECISION above) — NOT planner inference.

### Open question (to confirm before building)
Env per-repo in the Deploy step (chosen) vs a **combined 2-repo deploy** (one
wizard run deploying frontend + backend together). Leaning to the first
(small extension); the combined flow is a separate, bigger item.

### Build checklist (when greenlit)
- [ ] backend: preserve-on-update merge of encrypted secrets in
  `_submit_to_digitalocean` / `_build_envs` (GET app → reuse `EV[1:...]`).
- [ ] backend: expose the plan's env contract (keys + which are set on DO) to the
  console; accept operator-entered values on the deploy/redeploy request.
- [ ] console: env fields on the Deploy step (prefill non-secret; secret inputs;
  "•••set" on redeploy) + the same editor on the Settings tab.
- [ ] required-secret gate: warn/soft-block deploy when a required secret is unset.

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
2. ✅ **Deploy versioning + true provider rollback** — DONE. The History tab is now a **Versions** tab:
   each deploy is a numbered version (v1 = oldest) with status · time · cost ·
   plan summary, the latest tagged "current". **Rollback** calls
   `POST /workspaces/{ws}/deployments/{id}/rollback`, validates the target with
   DO, then executes DO native rollback with `{ deployment_id, skip_pin: true }`.
   New rows carry `rolled_back_from_id` and render as `current · rollback to vN`.
   **Rebuild plan** remains available via `/redeploy`; it reuses Ship's stored
   DeployPlan and builds again, with rows marked `rebuilt from vN`.
3. ✅ **Route/prefix coherence — base DONE.** Planner now has explicit
   `connections`; submit-time DO normalization rewrites frontend API-base envs
   to `$APP_URL/<service-route>` (e.g. `$APP_URL/api`) and ignores health-only
   routes. Still-open deeper case: `preserve_path_prefix=true` when backend
   source itself expects the public prefix.
4. **Internal wiring** — server→server, `DATABASE_URL`, queues (internal
   hostnames, not `$APP_URL`).
5. ✅ **Actions-workflow parity — DONE (`d64597e8`).** `ship-deploy-plan.yml`
   now mirrors the backend planner (byte-identical system prompt, file tree +
   layout + directory-diverse key files, max_tokens 8192, verify-guard + retry);
   `BUNDLE_VERSION` 0.40. See the "✅ Actions-planner parity" section near the
   top for the full verification + remaining deltas. **Open:** live-verify via a
   real no-key deploy + re-seed repos; Actions-path commit-pin.
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
