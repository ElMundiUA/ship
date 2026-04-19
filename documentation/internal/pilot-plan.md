# Ship pilot plan — WOW onboarding (Cloud SaaS, Model A)

> **Status:** **Day 1 + Day 2 + Day 3 shipped (2026-04-19); WOW wizard
> tightened to the planned 3-step shape (2026-04-20).** Pilot scope
> complete; live smoke test pending.
> **Source chats:** [Pilot scope discussion](442f31fa-34f0-47da-888b-a2d10a773f8e), [Day 1+2+3 build](48ef7ed3-881c-42e6-a823-1f670f4907ac)
> **Owner:** Denys / Ship core
> **Target:** 3-day pilot demo with WOW onboarding (sign-in → working dashboard in < 5 min)

---

## TL;DR

We pivot away from *"clone customer repos / sync git locally / run a worker"*
toward **Model A: Cloud SaaS + GitHub App + OAuth**.

- **Trackers (pilot scope):** GitHub Issues (Projects), Linear, Notion
- **CI / execution (pilot scope):** GitHub Actions only
- **Agents (pilot scope):** Cursor (IDE + Cloud), Codex (CLI / Cloud), Claude Code
- **Auth:** Auth0 with GitHub social connection (sign-in) + our GitHub App (repo access)
- **Hosting:** Bunny Magic Containers (FRA) + Neon Postgres (FRA) + Sentry + Auth0
- **What we drop for pilot:** Redis, ARQ worker, persistent volumes, `git_sync`, local repo cache, PAT-input integration UI as primary flow
- **On-prem / self-hosted / broker mode:** **postponed** — that is the future
*enterprise tier* with paywall (Snyk Broker / Sourcegraph Cloud Connect model).
No pilot work on it.

---

## Where we are now (handoff for the next session)

> **Read this first if you're picking the work up cold.** It's the only
> source of truth that's kept up to date as the build progresses; the
> per-day sections below describe the *plan* (with status badges), this
> section describes the *state of the repo right now*.

**Last updated:** 2026-04-20, after the WOW-wizard re-cut (see "Wizard
re-cut" section below).

### Wizard re-cut — ✅ shipped (2026-04-20)

After Day-3 we noticed the wizard had grown back into the pre-pivot
8-step shape (paste a URL → name a workspace → install GitHub → pick
repos → install workflows into the repo → tracker → seed knowledge into
the repo → mint CLI token → done). That violated the TL;DR ("we never
clone customer repos") and the WOW UX target. The cleanup:

- **Backend purge.** Deleted `backend/app/api/v1/routes/onboarding.py`,
  `backend/app/services/{repo_inspector,workflow_installer,knowledge_seeder}.py`,
  and `backend/tests/test_v1_onboarding.py`. The cloud backend has no
  code that can clone or commit into a customer repo any more.
- **JIT personal workspace.** `GET /v1/workspaces` now materialises a
  personal workspace on first hit (slug derived from the user id) so
  the wizard can land somewhere immediately after Auth0. New regression
  in `backend/tests/test_v1_workspaces.py`.
- **3-step wizard.** `console/src/app/onboarding/page.tsx` rewritten to
  render exactly three steps: `github` → `repos` → `tracker`. The page
  redirects unauthenticated visitors to `/login?next=/onboarding` and
  pulls the workspace id off the JIT call when `?ws=` is missing.
- **Console route purge.** Deleted `/api/onboard/{inspect,workspace,
  workflows,knowledge,mint-token,integration}` and the now-unused
  `inspectRepo / scaffoldDemoRepo / installWorkflows / seedKnowledge`
  exports from `console/src/lib/api/client.ts`.
- **Post-install short-circuit.** GitHub callback now redirects to
  `?step=repos&github=installed` (was `?step=github&...`) so the user
  doesn't have to hit one extra "Pick repos →" link before they can do
  anything. Tests updated.
- **Post-login routing.** `console/src/app/page.tsx` redirects to
  `/onboarding?step=github&ws=…` when the dashboard reports
  `active_repos === 0`. The mint-token UI moved off the wizard onto a
  permanent "Wire up your CLI" card under the dashboard plus a "CLI
  tokens" link in the header (both point at `/settings`, which already
  has a TokensPanel).

### Day 1 — ✅ shipped

Foundations are merged on `main`. Everything below works end-to-end with
the live test suite (162 passing). What landed:

- **Gateway interfaces** (`backend/app/integrations/gateway/`):
`CodeHostGateway`, `TrackerGateway`, `CIGateway`, `ChatGateway` —
typed protocols every vendor adapter implements. `RepoRef`,
`PullRequestRef`, `TicketRef` are the shared discriminated types.
- **GitHub App backend** (`backend/app/integrations/github/`):
  - `app_auth.py` — RS256 JWT minting + per-installation token cache
  (~1h TTL, invalidated on reinstall)
  - `oauth.py` — signed `state` token (HS256 over workspace_id + nonce
    - exp) and install URL builder
  - `webhook.py` — HMAC-SHA256 `X-Hub-Signature-256` verification
  - `code_host_adapter.py` — first cut of `CodeHostGateway` over the
  GitHub REST API using installation tokens
- **Routes** (`backend/app/api/v1/routes/github_app.py`):
  - `POST /v1/integrations/github/install/start` (admin-only, returns
  `install_url` + `state`)
  - `GET /v1/integrations/github/install/callback` (no auth, validates
  `state`, persists `GitHubInstallation`, audit-logs, redirects to
  `<console>/onboarding?step=github&github=installed`; on tampered
  state redirects with `?error=bad_state`)
  - `POST /v1/webhooks/github` (signature-verified;
  `installation` / `installation_repositories` handled, others ack'd
  for Day 3)
- **DB** (`backend/migrations/versions/0003_github_installations.py`):
new `github_installations` table — `(workspace_id, installation_id*, account_id, account_login, account_type, repository_selection, settings JSONB, installed_at, suspended_at)`. Unique on
`installation_id`, indexed on `workspace_id`.
- **Settings** (`backend/app/core/config.py`): `GITHUB_APP_ID`,
`GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_CLIENT_ID`,
`GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_WEBHOOK_SECRET`,
`GITHUB_APP_SLUG` (default `ship`), plus `SHIP_CONSOLE_URL`
(browser-facing console origin, used for callback redirects).
- **Console wizard** (`console/src/app/onboarding/page.tsx` +
`console/src/app/api/onboard/github-install/route.ts`):
new `step=github` between `workspace` and `workflows`. Renders the
install CTA, handles `?github=installed` (success banner),
`?github=request` (awaiting org-admin), and `?error=<code>` (mapped
via `GITHUB_ERRORS`). Workspace step now redirects to `step=github`
instead of jumping straight to `workflows`.
- **Migration env fix** (`backend/migrations/env.py`): honours an
`sqlalchemy.url` already injected by the Alembic `Config` (the
pytest fixture sets it to a local DB) before falling back to
`Settings`. Prevents accidental migration against the production
Neon DSN baked into `.env` while running local tests.
- **Tests**: `test_github_app_jwt.py`, `test_github_app_oauth.py`,
`test_github_app_webhook.py`, `test_v1_github_app.py` cover unit
  - integration paths (state minting/verification, signature
  verification, install start/callback redirects, webhook persistence
  of `installation` events). Suite-wide: 162 passing.
- **Operator docs** (`documentation/internal/github-app-setup.md`):
full one-time GitHub-side checklist (App registration, permissions,
events, secrets, env, smoke test, local dev via cloudflared, key
rotation). Linked from the Day 1 section below.

### Open items / known gaps before Day 2

1. **Auth0 GitHub-social-login** is *not* wired yet — the install flow
  above works under `SHIP_AUTH_MODE=local`. Production cutover needs
   the Auth0 dashboard click documented in [auth0-setup.md](../auth0-setup.md)
   plus a smoke run in `auth0` mode.
2. `**code_host_adapter.py`** is intentionally minimal (enough to cover
  the wizard's needs). Day 2's "list available repos" endpoint will
   be the next consumer that exercises it for real.
3. **Webhook handlers for `pull_request` / `workflow_run` / `check_run`
  / `pull_request_review`** verify signatures and ack 200, but don't
   touch DB yet. Day 3 lands the actual handlers.
4. **PAT-input integration UI** still exists in the console as a
  secondary flow for non-GitHub trackers; we'll leave it alone until
   Day 2 introduces tracker OAuth.
5. **No rate-limiting on `/v1/webhooks/github`** beyond the global
  limiter — fine for pilot, revisit before opening to public installs
   at scale.

### Day 2 — ✅ shipped

Repo picker, Linear + Notion OAuth, Code Map MVP all on `main`. Suite
green at 180 passing. What landed:

- `**RepoSummary**` added to `CodeHostGateway`; the GitHub adapter
paginates `/installation/repositories` (max 500) and surfaces
default branch, visibility, and `external_id` for the picker UI.
- `**workspace_repos` table** (migration `0004_workspace_repos.py`) with
`(workspace_id, provider, external_id)` unique key + FK to
`github_installations`. Stores the activated set so default
pipelines on Day 3 can iterate it without re-hitting GitHub.
- **Repo picker API** (`backend/app/api/v1/routes/repos.py`):
  - `GET /v1/workspaces/{ws}/repos/available` — live App-installation
  list with an `activated` flag per row.
  - `GET /v1/workspaces/{ws}/repos` — currently activated set (DB).
  - `POST /v1/workspaces/{ws}/repos/activate` — replaces the set,
  audit-logs the diff, rejects external_ids the App can't see (422).
  - `GET /v1/workspaces/{ws}/repos/{id}/code-map` — MVP truncated file
  list via the GitHub Trees API (cap 5,000).
- **Linear OAuth** (`backend/app/integrations/linear/`):
  - `oauth.py` — signed state JWT (HS256 over `JWT_SECRET`), authorize
  URL builder (`response_type=code`, `actor=user`, `prompt=consent`),
  `exchange_code_for_token` against `https://api.linear.app/oauth/token`.
  - `tracker_adapter.py` — GraphQL implementation of
  `TrackerGateway`: `list_tickets`, `transition`, `comment`.
  - `routes/linear_oauth.py`:
  `POST /v1/integrations/linear/install/start` (admin-only, 503 if
  creds missing) +
  `GET /v1/integrations/linear/install/callback` (public, validates
  state, exchanges code, persists `Integration{kind="linear"}` with
  encrypted token, audit-logs, redirects back to
  `<console>/onboarding?step=tracker&linear=connected`).
- **Notion OAuth** (`backend/app/integrations/notion/`):
  - Same shape as Linear, with the Notion-specific bits: HTTP Basic
  auth on the token endpoint, `Notion-Version: 2025-09-03` header,
  workspace metadata stored in the integration `config` so the
  console can show "connected to Acme" without re-asking.
  - `tracker_adapter.py` — REST adapter; uses `/search` for ticket
  listing because Notion has no first-class issue type, and patches
  `properties.Status` on `transition`.
  - `routes/notion_oauth.py`: same start + callback contract as Linear.
- **Settings** (`backend/app/core/config.py`): `LINEAR_CLIENT_ID`,
`LINEAR_CLIENT_SECRET`, `LINEAR_OAUTH_SCOPES` (default
`read,write,issues:create,comments:create`), `NOTION_CLIENT_ID`,
`NOTION_CLIENT_SECRET`. Both pairs hard-503 on the start endpoint
when missing so ops sees a clean "wire env vars" error instead of a
generic 500.
- **Console wizard**:
  - New `step=repos` between `github` and `workflows`. Server-renders
  the available list, ticks already-activated rows; submit posts to
  `/api/onboard/repos-activate` which delegates to the backend and
  bounces forward to `workflows`.
  - `step=tracker` rewritten: 3 OAuth/skip tiles (Linear, Notion,
  GitHub Issues "already connected"). Submitting a vendor tile
  posts to `/api/onboard/tracker-install` which calls the matching
  backend `install/start` and 303-redirects the browser to the
  vendor's authorize URL. The vendor callback bounces back into
  `?step=tracker&{linear,notion}=connected`. PAT-input form is
  retired; `INTEGRATION_PRESETS` removed from the wizard.
- **Tests**: `test_v1_repos.py` (picker + activate + code-map),
`test_v1_linear_oauth.py` (start + callback round-trip with
`exchange_code_for_token` monkey-patched), `test_v1_notion_oauth.py`
(mirror). 180 passing across the whole backend.
- **Docker**: rebuilt `ship-server` after image refresh exposed missing
`integrations/` package; `alembic.ini` had `sqlalchemy.url` cleared
so `ALEMBIC_DATABASE_URL` from compose is no longer overridden, and
`migrations/env.py` now strips whitespace before deciding whether to
fall back to `Settings.sync_database_url`.

### Day 3 — ✅ shipped

The dashboard surface is live and the five baked-in pipelines come up
automatically the moment a tenant activates their first repo. 193
backend tests passing.

What landed:

- **DB:** new `pipelines`, `pipeline_runs`, `pull_requests`, and
  `workflow_runs` tables (`backend/app/db/models/pipelines.py`,
  migration `0005_pipelines`). Each cascades off `workspaces.id` so a
  workspace delete sweeps the dashboard data with it.
- **Default pipelines (`backend/app/services/default_pipelines.py`):**
  hard-coded catalog of five lanes — `pr_review`, `daily_standup`,
  `code_map`, `tech_debt`, `self_heal` — keyed off the existing
  workflow artifact slugs (`pr-and-ci-gate`, `scheduled-sdlc-lane`,
  …). `seed_default_pipelines` is idempotent on `(workspace, kind)`
  and additive only (never re-enables a row a tenant turned off).
  `POST /v1/workspaces/{ws}/repos/activate` now calls it inside the
  same transaction and audits how many lanes the call materialised.
- **Pipelines API (`backend/app/api/v1/routes/pipelines.py`):**
  - `GET  /v1/workspaces/{ws}/pipelines` — list (members)
  - `PATCH /v1/workspaces/{ws}/pipelines/{id}` — toggle enabled
    (admin-only, no-op write skips the audit row)
  - `POST  /v1/workspaces/{ws}/pipelines/{id}/runs` — synchronous
    stub runner: insert `running` → mark `succeeded` → return.
    Disabled pipelines reject with 409. Updates `last_run_*` on the
    parent for cheap card rendering.
  - `GET  /v1/workspaces/{ws}/pipelines/{id}/runs?limit=10` — recent
    history, capped at 20.
- **Dashboard summary (`backend/app/api/v1/routes/dashboard.py`):**
  one denormalised endpoint `GET /v1/workspaces/{ws}/dashboard` that
  returns counts + the five pipelines + last 10 PRs / workflow runs /
  pipeline runs. Replaces N round-trips per render with one.
- **Webhook handlers** in
  `backend/app/api/v1/routes/github_app.py` extended:
  - `pull_request` → upsert `PullRequest` for the matching
    `WorkspaceRepo`. Synthetic `state="merged"` when GitHub closes a
    PR with `merged=true`. Drops silently for repos the workspace
    hasn't activated.
  - `workflow_run` → upsert `WorkflowRun` with status / conclusion /
    head_sha / actor. Same activation-gate behaviour.
- **Console dashboard** at `console/src/app/page.tsx`:
  - When `SHIP_API_URL` is set and the user has a workspace, the page
    fetches `/dashboard` server-side and renders the new
    `DashboardLive` component (`console/src/components/dashboard-live.tsx`).
  - Five pipeline cards with toggle + "Run now" forms posting to the
    new `/api/dashboard/{toggle,run}-pipeline/route.ts` Next.js
    handlers. No JS required — same form-driven pattern as the
    Day-2 onboarding wizard.
  - Live PR table (with author, branch state, "merged"/"draft"
    chips), workflow-runs strip, and pipeline-runs table.
  - Mock dashboard kept as fallback for the marketing-style preview
    deployment with no backend.
- **Frontend client** (`console/src/lib/api/client.ts`) gained
  `listPipelines`, `togglePipeline`, `runPipeline`,
  `listPipelineRuns`, and `getDashboard`.
- **Tests:** new files
  `backend/tests/test_v1_pipelines.py` (7 tests),
  `backend/tests/test_v1_dashboard.py` (2 tests),
  `backend/tests/test_v1_webhooks.py` (4 tests). Cover the
  default-seed contract, toggle/no-op, run + 409-when-disabled,
  webhook upsert and the inactive-repo drop-path. Total backend suite
  now 193 passing.

### Day 3 — open items / smoke-test work

1. **End-to-end smoke test** — wire the GitHub App against a real
   pilot tenant + Notion DB + Linear team and walk the WOW
   onboarding (Auth0 → GitHub App install → repo picker → tracker
   OAuth → dashboard). The build is ready; this is just the live
   demo run.
2. **Sentry release tag** — verify both backend and console emit
   events with the right `release` tag (`sha-<short>`) once the
   pilot tenant is provisioned. Code path is already wired; Day-3
   work is just the verification.
3. **Operator docs** — `documentation/internal/{github,linear,notion}-oauth-setup.md`
   describing how to register each OAuth app for the pilot tenant.
4. **Real pipeline runner** — the `POST /pipelines/{id}/runs` body is
   a stub. When package #2 brings the worker back, swap the
   synchronous "succeed immediately" path for the real lane
   execution. The HTTP shape stays the same.
5. **Notion database picker** + **Linear webhook subscription** still
   deferred (carried over from Day-2 open items).
6. **Code-map persistence** still deferred — the MVP returns the
   file list inline. S3 caching + refresh cron is post-pilot.

---

## Why this shape

1. **Wow-onboarding is the product moat for SMB.** Vercel / Linear / Cursor /
  Railway all win first impression by *"Sign in with GitHub → Authorize →
   Done."* If we make the user paste a PAT or wait 3 days for IT, we lose the
   demo before showing anything cool.
2. **Security paranoia is real but not for our pilot customer profile.** The
  first paying customers won't be regulated banks. Regulated comes later, and
   they'll pay for broker mode.
3. **Architecture stays escape-hatched.** All adapters live behind a `Gateway`
  interface (typed namespaces, option B) so a future `BrokerGateway` plugs in
   without touching domain code.

---

## Architecture decision: Gateway pattern (typed namespaces)

```
backend/app/integrations/
├── gateway/
│   ├── code_host.py        # interface: list_repos, get_pr, merge_pr, ...
│   ├── tracker.py          # interface: list_tickets, transition, comment, ...
│   ├── ci.py               # interface: list_runs, rerun, get_logs, ...
│   └── chat.py             # interface: post_message, attach_card, ...
├── github/                 # pilot day 1 + 2
│   ├── code_host_adapter.py
│   ├── tracker_adapter.py  # GitHub Issues / Projects
│   ├── ci_adapter.py       # GitHub Actions
│   └── oauth.py            # GitHub App install flow + webhook receiver
├── linear/                 # pilot day 2
│   ├── tracker_adapter.py
│   └── oauth.py
├── notion/                 # pilot day 2
│   ├── tracker_adapter.py
│   └── oauth.py
└── (future) azure_devops/, teams/, jira/, slack/, broker/
```

- All controllers / pipeline runners / resolvers depend on the **interface**,
not the concrete adapter.
- DI picks adapter per `workspace.code_host_kind` / `tracker_kind`.
- IDs are **discriminated unions** (`{kind: "github", owner, repo}` vs
`{kind: "ado", org, project, repo}`) so we don't lose vendor-specific
hierarchy when ADO arrives in package #2.

---

## What we cut from the existing codebase


| Component                                      | Action                                        | Reason                                                                                                                                                                                     |
| ---------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/app/workers/git_sync.py`              | **Delete** (don't flag-gate)                  | We never clone customer repos in cloud SaaS; broker would clone on their side, not ours                                                                                                    |
| `backend/app/workers/main.py` `repo_sync` cron | **Delete**                                    | Same                                                                                                                                                                                       |
| `repo-cache` volume + `REPO_CACHE_ROOT` env    | **Delete**                                    | Same                                                                                                                                                                                       |
| `cron_probe_pending_secrets`                   | **Inline into endpoint**                      | `PUT /integrations/{kind}` does probe synchronously and returns status in same response. Add `POST /integrations/{kind}/reprobe` for retry button. UX gets *better* (instant vs 30s wait). |
| `heartbeat` cron                               | **Delete**                                    | `/healthz` covers it                                                                                                                                                                       |
| ARQ worker (`backend/app/workers/main.py`)     | **Don't deploy in cloud**                     | After the above three cuts, no jobs left. Keep code on disk under `--profile worker` for future on-prem; cloud topology has no worker container.                                           |
| Redis                                          | **Don't deploy in cloud**                     | Only used as ARQ broker; no worker → no Redis. Rate limiter is already in-memory. Remove `REDIS_URL` from required `Settings`, make it `str | None`.                                       |
| `docker-compose.yml` worker + redis services   | Move under `--profile worker --profile redis` | Keeps local dev option (`docker compose --profile worker --profile redis up`); default `up` runs lean stack                                                                                |
| `docker-compose.prod.yml`                      | Remove worker + redis services entirely       | Cloud topology                                                                                                                                                                             |
| Custom signup/login UI                         | **Hide behind feature flag**                  | Auth0 Universal Login with "Continue with GitHub" is the only visible path                                                                                                                 |
| PAT-input integration forms (Notion, GitHub)   | **Hide from primary onboarding**              | Replaced by OAuth / App flows; endpoints stay for CLI/CI use cases                                                                                                                         |
| `artifact-repos` manual clone-URL form         | **Replace**                                   | New flow: pick repos from GitHub App installation API                                                                                                                                      |
| `SHIP_REPO_INTEGRATION_MODE` env flag          | **Don't introduce**                           | We discussed it earlier; with `git_sync` gone, the flag is unnecessary                                                                                                                     |


---

## What we KEEP

- **PAT mint endpoints** (`/v1/tokens`) — required for our own CLI, GitHub
Actions, agents calling our API. Just hidden from primary onboarding UI.
- **Audit log** API + UI.
- **Multi-tenant isolation tests.**
- **In-memory rate limiter** on auth + token-mint endpoints.
- **Auth0** as the auth backbone (now with GitHub social connection front
and centre).
- **Sentry** on backend + console.
- **Caddy + docker-compose.prod.yml** for self-hosted single-VPS use case
(we still document it; future enterprise might want it).
- **All `agent-rules-`* collections** + `shipctl init --copy-rules` flow.
- **Workflows** (`pr-and-ci-gate`, `pipeline-self-heal`, etc.) — they become
the **default pipelines** auto-created on first repo activation.

---

## Cloud topology

```
Bunny Magic Containers (Frankfurt):
  ├── ship-backend   (FastAPI)         1 container, CDN endpoint, port 8100→443
  └── ship-console   (Next.js)         1 container, CDN endpoint, port 3001→443

External managed:
  ├── Neon Postgres  (eu-central-1)    pooled DSN for runtime, direct DSN for alembic
  ├── Auth0          (EU tenant)       GitHub social connection enabled
  ├── GitHub App     "Ship" (our org)  multi-tenant, public install
  ├── Linear OAuth   "Ship"            public app
  ├── Notion         "Ship"            public OAuth integration
  └── Sentry         (our project)     backend + console DSNs
```

**Zero** of: Redis, Upstash, ARQ worker, persistent volumes, repo-cache,
secret stores beyond Auth0 + our encrypted DB columns.

---

## WOW onboarding flow (target UX)

```
1. Land on https://ship.<domain>
   ↓
2. Click "Continue with GitHub"
   → Auth0 Universal Login → GitHub OAuth → back
   ↓ [JIT user + workspace created, ~3s]
3. "Connect your code"
   Click "Install Ship on GitHub"
   → GitHub App install screen → pick repos → Install
   ↓ [installation_id stored, webhooks armed, ~5s]
4. "Connect your tracker" (optional, skippable)
   Choose: [Linear] [Notion] [GitHub Issues — already connected]
   → vendor OAuth → pick workspace/database → Authorize
   ↓
5. Dashboard — already populated:
   - 5 default pipelines, all toggled on
   - Last 10 PRs from selected repos
   - Last 10 tickets from selected tracker
   - "Run PR review now" button → result in UI in 10–30s
```

**Total:** 3-5 clicks, < 5 minutes, zero PAT copy-paste.

---

## Default pipelines (auto-created on repo activation)


| Pipeline             | Backed by artifact              | Trigger                  | Purpose                                     |
| -------------------- | ------------------------------- | ------------------------ | ------------------------------------------- |
| `pr-review`          | `workflow/pr-and-ci-gate`       | `pull_request` webhook   | LLM review on PR open + push                |
| `daily-standup`      | `workflow/scheduled-sdlc-lane`  | cron 09:00 local         | Yesterday → today digest from PRs + tickets |
| `code-map-refresh`   | (new)                           | repo activation + weekly | Build code map JSON via GitHub Trees API    |
| `tech-debt-scan`     | `workflow/parallel-audit-lanes` | weekly cron              | Audit lanes → tickets in tracker            |
| `pipeline-self-heal` | `workflow/pipeline-self-heal`   | `workflow_run` failure   | Auto-fix red CI                             |


User can toggle on/off, see last run status, click "Run now". MVP: synchronous
fire-and-forget via FastAPI `BackgroundTasks` (no worker needed).

---

## Vendor coverage matrix (pilot scope)

### Trackers — **3 supported**


| Vendor                       | Auth                     | Webhooks                                                 | Status                |
| ---------------------------- | ------------------------ | -------------------------------------------------------- | --------------------- |
| **GitHub Issues / Projects** | covered by GitHub App    | included                                                 | ✅ pilot               |
| **Linear**                   | OAuth Application        | GraphQL webhooks (real-time)                             | ✅ pilot               |
| **Notion**                   | Public OAuth Integration | weak (Connections API beta); fallback to 60-120s polling | ⚠️ pilot, with caveat |


### CI — **1 supported**


| Vendor             | Auth                  | Events                      | Status  |
| ------------------ | --------------------- | --------------------------- | ------- |
| **GitHub Actions** | covered by GitHub App | `workflow_run`, `check_run` | ✅ pilot |


### Agents — **3 supported**


| Agent                       | Mode                   | Wow-flow for *connecting agent to Ship*                                                                  |
| --------------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------- |
| **Cursor** (IDE)            | interactive            | `shipctl init --agents cursor --copy-rules` writes `.cursor/rules/...`; PAT goes into `.cursor/mcp.json` |
| **Cursor Cloud Agent**      | cloud                  | `agent-rules-cursor-cloud` artifact + `.cursor/environments.json` with PAT in Cursor secrets             |
| **Codex** (CLI + Cloud)     | headless / cloud       | `.codex/SHIP_API.md` + PAT in env                                                                        |
| **Claude Code** (CLI / IDE) | interactive + headless | `CLAUDE.md` + PAT in env                                                                                 |


All three agents read our rule files and call our API via PAT. The `shipctl init --copy-rules` flow is already implemented and works.

### Out of scope for pilot

- Other agents (Aider, Continue, Cline, Windsurf, Zed, Gemini, OpenCode,
Copilot) — keep `agent-rules-*` collections, but no extra integration
- Slack — postponed to package #3
- Jira / GitLab / Bitbucket — postponed to package #3-4
- Azure DevOps + Teams — **package #2** (next sprint after pilot)
- All on-prem flavours — enterprise tier, future paid offering

---

## 3-day pilot build plan

### Day 1 — Foundations: Auth0 + GitHub App + Gateway interface

> **Status: ✅ shipped 2026-04-19.**
> Operator wiring lives in [github-app-setup.md](./github-app-setup.md);
> Auth0 wiring in [../auth0-setup.md](../auth0-setup.md). All items below
> are merged with tests (162 passing as of d1 close).

1. **Auth0 config:** enable GitHub social connection in our Auth0 EU tenant
  (5-min dashboard click). Update Universal Login screen.
   *Manual ops step — see [auth0-setup.md](../auth0-setup.md).*
2. **Console:** landing page with single "Continue with GitHub" button →
  Auth0 → callback → JIT workspace creation if first time.
3. **Register GitHub App "Ship"** in our org `ship-platform` (or wherever).
  Permissions: PRs read+write, contents read, metadata read, issues
   read+write, workflow_run read, checks read, webhooks. Public listing,
   multi-tenant install.
   *Manual ops step — see [github-app-setup.md](./github-app-setup.md).*
4. **Backend:** `backend/app/integrations/gateway/{code_host,tracker,ci,chat}.py`
  — abstract interfaces.
5. **Backend:** `backend/app/integrations/github/`:
  - `app_auth.py` — App JWT minting + per-installation token cache
  - `oauth.py` — signed `state` token + install URL builder
  - `webhook.py` — HMAC-SHA256 signature verification
  - `code_host_adapter.py` — implements `CodeHostGateway` via App tokens
  - routes: `POST /v1/integrations/github/install/start`,
  `GET /v1/integrations/github/install/callback`,
  `POST /v1/webhooks/github`
6. **DB migration:** new table `github_installations` (workspace_id,
  installation_id, account_login, account_id, account_type,
   repository_selection, installed_at, suspended_at).
   See `backend/migrations/versions/0003_github_installations.py`.
7. **Console:** onboarding step "Connect your GitHub" → button kicks
  `/api/onboard/github-install` → backend returns `install_url` →
   redirect to `github.com/apps/<slug>/installations/new`. Success
   bounces back to `?step=github&github=installed` so the user sees the
   "GitHub App installed" banner before moving on to workflows.

### Day 2 — Repo picker + tracker OAuth + Code Map MVP

> **Status: ✅ shipped (2026-04-19).** All bullets below landed; see the
> "Day 2 — ✅ shipped" handoff section above for the file-by-file
> rundown and the open items deferred to Day 3.

1. **Backend:** `GET /v1/repos/available` (live from installation API)
  - `POST /v1/repos/activate` (saves selection; default-pipelines
   auto-create deferred to Day 3).
2. **Console:** onboarding step "Pick repos" — checkbox list from live data.
3. **Linear OAuth:**
  - Register Ship OAuth Application at linear.app/settings → API
  - `backend/app/integrations/linear/oauth.py` start + callback
  - `linear/tracker_adapter.py` implements `TrackerGateway`
4. **Notion OAuth:**
  - Register public Ship integration at notion.so/my-integrations
  - `backend/app/integrations/notion/oauth.py` + `tracker_adapter.py`
  - Note: user must still share a database with the integration (Notion
  limitation); UI explains this in one screen
5. **Console:** onboarding step "Pick tracker" → choose vendor → OAuth →
  pick workspace/database/team → done.
6. **Code Map resolver MVP:** synchronous endpoint, GitHub Trees API,
  trims to 5k files preview for big repos.

### Day 3 — Default pipelines + dashboard + polish

> **Status: ✅ shipped (2026-04-19).** See the
> "Day 3 — ✅ shipped" handoff section above for the file-by-file
> list and the smoke-test work that's still queued.

1. **Backend:** auto-create 5 default pipelines on first `repos/activate`.
2. **Backend:** `POST /v1/pipelines/{id}/run` (sync via BackgroundTasks),
  `GET /v1/pipelines/{id}/runs` (history).
3. **Backend:** webhook handlers update PR/run statuses live in DB.
4. **Console:** main dashboard:
  - 5 pipeline cards with toggle, last-run status, "Run now"
  - Header metrics: PRs touched today, tickets synced, runs total
  - Live PR list, live ticket list
5. **Console:** polish onboarding wizard (progress bar, skip options).
6. **Sentry:** verify both backend + console emit events with right
  `release` tag (`sha-<short>`).
7. **Smoke test:** end-to-end on a real test repo + Notion DB + Linear team.

---

## Cuts to make BEFORE day 1 (refactor commits)

These need to land first to avoid building on outdated foundation. Estimate:
1-2 hours total.

1. `**refactor(infra): remove worker + redis from cloud topology`**
  - `docker-compose.yml`: worker + redis under `--profile worker / --profile redis`
  - `docker-compose.prod.yml`: delete worker + redis services
  - `backend/app/core/config.py`: `redis_url: str | None = None`
  - `backend/app/workers/main.py`: if no `REDIS_URL`, exit 0 with info log
  - Remove `REDIS_URL` from `.env.example` required block
2. `**refactor(integrations): probe secrets synchronously on save**`
  - Inline `cron_probe_pending_secrets` logic into `PUT /integrations/{kind}`
  - Add `POST /integrations/{kind}/reprobe`
  - Tests: success-path + failure-path
3. `**refactor(repo): drop git_sync worker and repo-cache**`
  - Delete `backend/app/workers/git_sync.py`
  - Delete `cron_sync_pending_repos` from `backend/app/workers/main.py`
  - Delete `REPO_CACHE_ROOT`, `REPO_SYNC_INTERVAL_MINUTES` from config
  - Update tests
4. `**feat(db): asyncpg pooler-safe engine config (Neon pgbouncer compat)**`
  - `backend/app/db/engine.py`: when host contains `-pooler`, set
   `connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0}`
  - Smoke test with synthetic URL

After these four commits, the codebase is ready for the 3-day pilot build.

---

## Bunny / Neon / Auth0 setup checklist

### Neon (do first — backend won't start without DSN)

1. neon.tech → New project `ship-pilot`, region **AWS eu-central-1 (Frankfurt)**.
2. Connection Details → grab two DSNs:
  - **Pooled** (host has `-pooler`) → `DATABASE_URL` =
   `postgresql+asyncpg://USER:PASS@HOST-pooler.eu-central-1.aws.neon.tech/DB?ssl=require`
  - **Direct** (no `-pooler`) → `ALEMBIC_DATABASE_URL` =
  `postgresql+psycopg://USER:PASS@HOST.eu-central-1.aws.neon.tech/DB?sslmode=require`

### Auth0

1. EU tenant → Authentication → Social → enable **GitHub** connection.
2. Configure GitHub OAuth App (separate from our **GitHub App**!) for
  Auth0's social login. Callback: `https://<auth0-domain>/login/callback`.
3. Application → Regular Web App → grab `client_id`, `client_secret`,
  `domain`.
4. Allowed Callback: `https://ship.<your-domain>/api/auth/callback`.
5. Allowed Logout: `https://ship.<your-domain>`.

### GitHub App "Ship" (the real wow-flow app)

1. Our GitHub org → Settings → Developer settings → GitHub Apps → New.
2. Name: `Ship`. Homepage: `https://ship.<your-domain>`.
3. Webhook URL: `https://api.ship.<your-domain>/v1/webhooks/github`.
4. Webhook secret: generate, store as `GITHUB_APP_WEBHOOK_SECRET`.
5. Permissions:
  - Repository: Pull requests R+W, Contents R, Metadata R, Issues R+W,
   Workflows R, Checks R, Actions R
  - Organization: Members R (for member sync, optional)
6. Subscribe to events: `pull_request`, `push`, `issues`, `workflow_run`,
  `check_run`, `installation`, `installation_repositories`.
7. Where: Any account. Public.
8. Generate **Private key** (.pem) → store as `GITHUB_APP_PRIVATE_KEY`.
9. Copy `App ID` → `GITHUB_APP_ID`.
10. Copy `Client ID` + `Client Secret` (for OAuth user identification within
  App context) → `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET`.

### Linear

1. linear.app/settings → API → OAuth Applications → New.
2. Redirect URI: `https://api.ship.<your-domain>/v1/integrations/linear/callback`.
3. Webhooks: `https://api.ship.<your-domain>/v1/webhooks/linear`.
4. Scopes: `read`, `write`, `issues:create`, `comments:create`.
5. Grab `client_id` + `client_secret` → `LINEAR_CLIENT_ID` / `LINEAR_CLIENT_SECRET`.

### Notion

1. notion.so/my-integrations → New integration.
2. Type: **Public** (so any Notion workspace can install).
3. Redirect URI: `https://api.ship.<your-domain>/v1/integrations/notion/callback`.
4. Capabilities: Read content, Update content, Insert content, Read user info.
5. Grab `oauth_client_id`, `oauth_client_secret` → `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET`.

### Sentry

Already covered in `documentation/operating.md`; no changes for pilot.

### Bunny Magic Containers

1. **ship-backend** app:
  - Image: `dekus/ship-backend:sha-<latest>`
  - **No** persistent volume
  - Endpoint: CDN, origin port 8100, public 443
  - Region: Frankfurt
  - Custom hostname: `api.ship.<your-domain>`
  - Env vars (see full list below)
2. **ship-console** app:
  - Image: `dekus/ship-console:sha-<latest>`
  - **No** persistent volume
  - Endpoint: CDN, origin port 3001, public 443
  - Region: Frankfurt
  - Custom hostname: `ship.<your-domain>`
  - Env vars (see full list below)

### Backend env vars (Bunny `ship-backend`)

```
SHIP_PUBLIC_URL=https://api.ship.<your-domain>
SHIP_AUTH_MODE=auth0
JWT_SECRET=<openssl rand -hex 48>                      # for our internal PATs
JWT_ALGORITHM=HS256
JWT_TTL_SECONDS=43200
ENCRYPTION_KEY=<Fernet.generate_key()>

DATABASE_URL=postgresql+asyncpg://...?ssl=require       # Neon pooled
ALEMBIC_DATABASE_URL=postgresql+psycopg://...?sslmode=require  # Neon direct

# Auth0
AUTH0_DOMAIN=<tenant>.eu.auth0.com
AUTH0_AUDIENCE=https://api.ship.<your-domain>
AUTH0_ISSUER=https://<tenant>.eu.auth0.com/

# GitHub App
GITHUB_APP_ID=<numeric>
GITHUB_APP_PRIVATE_KEY=<-----BEGIN RSA PRIVATE KEY----- ... ----->
GITHUB_APP_CLIENT_ID=Iv1.xxxxxxxxxxxx
GITHUB_APP_CLIENT_SECRET=xxxxxxxx
GITHUB_APP_WEBHOOK_SECRET=<random hex>

# Linear
LINEAR_CLIENT_ID=xxxxxxxx
LINEAR_CLIENT_SECRET=xxxxxxxx

# Notion
NOTION_CLIENT_ID=xxxxxxxx
NOTION_CLIENT_SECRET=xxxxxxxx

# Sentry
SENTRY_DSN=https://...@sentry.io/...
SENTRY_ENVIRONMENT=pilot
SHIP_VERSION=sha-<short>
SENTRY_SERVICE_NAME=ship-server
```

### Console env vars (Bunny `ship-console`)

```
NEXT_PUBLIC_SHIP_API_URL=https://api.ship.<your-domain>
NEXT_PUBLIC_AUTH0_DOMAIN=<tenant>.eu.auth0.com
NEXT_PUBLIC_AUTH0_CLIENT_ID=<console SPA client id>
NEXT_PUBLIC_AUTH0_AUDIENCE=https://api.ship.<your-domain>
AUTH0_SECRET=<openssl rand -hex 32>                     # for cookie encryption (server-side)
AUTH0_BASE_URL=https://ship.<your-domain>
AUTH0_ISSUER_BASE_URL=https://<tenant>.eu.auth0.com
AUTH0_CLIENT_ID=<console regular web app client id>
AUTH0_CLIENT_SECRET=<...>

NEXT_PUBLIC_SENTRY_DSN=https://...@sentry.io/...
SENTRY_ENVIRONMENT=pilot
SHIP_VERSION=sha-<short>
SENTRY_SERVICE_NAME=ship-console
```

---

## Roadmap beyond pilot


| Package                         | When                              | Adds                                                                                         |
| ------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------- |
| **#1 pilot** (this plan)        | this week                         | GitHub App + GitHub Actions + Notion + Linear                                                |
| **#2 MS-stack**                 | next sprint                       | Azure DevOps Services (Repos + Pipelines + Boards) + Microsoft Teams via single Entra ID app |
| **#3 Atlassian + Slack**        | 2-3 weeks                         | Jira Cloud + Slack                                                                           |
| **#4 Long-tail**                | on demand                         | GitLab Cloud, Bitbucket Cloud, ClickUp, Asana, Sentry-as-source, Datadog-as-source           |
| **#5 Enterprise tier (broker)** | when first paying enterprise asks | Self-hosted broker for on-prem GHES / GitLab SM / ADO Server. **Paywall.**                   |


---

## Open questions to resolve before building

1. **GitHub App registration owner:** which GitHub org hosts "Ship" — existing
  `@elmundi/...` or new dedicated `ship-platform`?
2. **Domain for pilot:** which `ship.<domain>` and `api.ship.<domain>`?
3. **Auth0 tenant:** reuse Mailosaur-tied tenant from earlier conversation, or
  spin a fresh one for pilot?
4. **Free-tier limits awareness:**
  - Neon free: 0.5 GB storage, 1 compute unit — fine for pilot
  - Auth0 free: 7,500 MAU — fine
  - Linear OAuth app: free
  - Notion OAuth: free
  - GitHub App: free
  - Bunny MC: paid per container hour
5. **Pilot customer:** confirm the test client's stack (GitHub? Notion? Linear?
  all three?). If only GitHub + Notion, we still build Linear adapter for
   the demo but don't need it live for the actual pilot.

---

## Reference: chats this plan compiles

- [Phase 2 push + deployment runbook + Bunny CI](442f31fa-34f0-47da-888b-a2d10a773f8e)
- Architecture pivot to Model A discussed in same chat session, sub-thread
on security model and onboarding UX.

When picking this plan back up after a restart, read this file first, then
optionally pull the chat transcript for nuance.