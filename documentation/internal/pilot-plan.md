# Ship pilot plan — WOW onboarding (Cloud SaaS, Model A)

> **Status:** planning, not implemented yet. Compiled from session 2026-04-19.
> **Source chats:** [Pilot scope discussion](442f31fa-34f0-47da-888b-a2d10a773f8e)
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

| Component | Action | Reason |
|---|---|---|
| `backend/app/workers/git_sync.py` | **Delete** (don't flag-gate) | We never clone customer repos in cloud SaaS; broker would clone on their side, not ours |
| `backend/app/workers/main.py` `repo_sync` cron | **Delete** | Same |
| `repo-cache` volume + `REPO_CACHE_ROOT` env | **Delete** | Same |
| `cron_probe_pending_secrets` | **Inline into endpoint** | `PUT /integrations/{kind}` does probe synchronously and returns status in same response. Add `POST /integrations/{kind}/reprobe` for retry button. UX gets *better* (instant vs 30s wait). |
| `heartbeat` cron | **Delete** | `/healthz` covers it |
| ARQ worker (`backend/app/workers/main.py`) | **Don't deploy in cloud** | After the above three cuts, no jobs left. Keep code on disk under `--profile worker` for future on-prem; cloud topology has no worker container. |
| Redis | **Don't deploy in cloud** | Only used as ARQ broker; no worker → no Redis. Rate limiter is already in-memory. Remove `REDIS_URL` from required `Settings`, make it `str \| None`. |
| `docker-compose.yml` worker + redis services | Move under `--profile worker --profile redis` | Keeps local dev option (`docker compose --profile worker --profile redis up`); default `up` runs lean stack |
| `docker-compose.prod.yml` | Remove worker + redis services entirely | Cloud topology |
| Custom signup/login UI | **Hide behind feature flag** | Auth0 Universal Login with "Continue with GitHub" is the only visible path |
| PAT-input integration forms (Notion, GitHub) | **Hide from primary onboarding** | Replaced by OAuth / App flows; endpoints stay for CLI/CI use cases |
| `artifact-repos` manual clone-URL form | **Replace** | New flow: pick repos from GitHub App installation API |
| `SHIP_REPO_INTEGRATION_MODE` env flag | **Don't introduce** | We discussed it earlier; with `git_sync` gone, the flag is unnecessary |

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
- **All `agent-rules-*` collections** + `shipctl init --copy-rules` flow.
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

| Pipeline | Backed by artifact | Trigger | Purpose |
|---|---|---|---|
| `pr-review` | `workflow/pr-and-ci-gate` | `pull_request` webhook | LLM review on PR open + push |
| `daily-standup` | `workflow/scheduled-sdlc-lane` | cron 09:00 local | Yesterday → today digest from PRs + tickets |
| `code-map-refresh` | (new) | repo activation + weekly | Build code map JSON via GitHub Trees API |
| `tech-debt-scan` | `workflow/parallel-audit-lanes` | weekly cron | Audit lanes → tickets in tracker |
| `pipeline-self-heal` | `workflow/pipeline-self-heal` | `workflow_run` failure | Auto-fix red CI |

User can toggle on/off, see last run status, click "Run now". MVP: synchronous
fire-and-forget via FastAPI `BackgroundTasks` (no worker needed).

---

## Vendor coverage matrix (pilot scope)

### Trackers — **3 supported**

| Vendor | Auth | Webhooks | Status |
|---|---|---|---|
| **GitHub Issues / Projects** | covered by GitHub App | included | ✅ pilot |
| **Linear** | OAuth Application | GraphQL webhooks (real-time) | ✅ pilot |
| **Notion** | Public OAuth Integration | weak (Connections API beta); fallback to 60-120s polling | ⚠️ pilot, with caveat |

### CI — **1 supported**

| Vendor | Auth | Events | Status |
|---|---|---|---|
| **GitHub Actions** | covered by GitHub App | `workflow_run`, `check_run` | ✅ pilot |

### Agents — **3 supported**

| Agent | Mode | Wow-flow for *connecting agent to Ship* |
|---|---|---|
| **Cursor** (IDE) | interactive | `shipctl init --agents cursor --copy-rules` writes `.cursor/rules/...`; PAT goes into `.cursor/mcp.json` |
| **Cursor Cloud Agent** | cloud | `agent-rules-cursor-cloud` artifact + `.cursor/environments.json` with PAT in Cursor secrets |
| **Codex** (CLI + Cloud) | headless / cloud | `.codex/SHIP_API.md` + PAT in env |
| **Claude Code** (CLI / IDE) | interactive + headless | `CLAUDE.md` + PAT in env |

All three agents read our rule files and call our API via PAT. The `shipctl
init --copy-rules` flow is already implemented and works.

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

1. **Auth0 config:** enable GitHub social connection in our Auth0 EU tenant
   (5-min dashboard click). Update Universal Login screen.
2. **Console:** landing page with single "Continue with GitHub" button →
   Auth0 → callback → JIT workspace creation if first time.
3. **Register GitHub App "Ship"** in our org `ship-platform` (or wherever).
   Permissions: PRs read+write, contents read, metadata read, issues
   read+write, workflow_run read, checks read, webhooks. Public listing,
   multi-tenant install.
4. **Backend:** `backend/app/integrations/gateway/{code_host,tracker,ci,chat}.py`
   — abstract interfaces.
5. **Backend:** `backend/app/integrations/github/`:
   - `oauth.py` — install start/callback endpoints
   - `code_host_adapter.py` — implements `CodeHostGateway` via PyGithub +
     httpx + JWT App auth
   - webhook receiver `/v1/webhooks/github`
6. **DB migration:** new table `github_installations` (workspace_id,
   installation_id, account_login, account_id, installed_at).
7. **Console:** onboarding step "Connect your GitHub" → button kicks
   `/v1/integrations/github/install/start`.

### Day 2 — Repo picker + tracker OAuth + Code Map MVP

1. **Backend:** `GET /v1/repos/available` (live from installation API)
   + `POST /v1/repos/activate` (saves selection + creates default pipelines).
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

1. **`refactor(infra): remove worker + redis from cloud topology`**
   - `docker-compose.yml`: worker + redis under `--profile worker / --profile redis`
   - `docker-compose.prod.yml`: delete worker + redis services
   - `backend/app/core/config.py`: `redis_url: str | None = None`
   - `backend/app/workers/main.py`: if no `REDIS_URL`, exit 0 with info log
   - Remove `REDIS_URL` from `.env.example` required block
2. **`refactor(integrations): probe secrets synchronously on save`**
   - Inline `cron_probe_pending_secrets` logic into `PUT /integrations/{kind}`
   - Add `POST /integrations/{kind}/reprobe`
   - Tests: success-path + failure-path
3. **`refactor(repo): drop git_sync worker and repo-cache`**
   - Delete `backend/app/workers/git_sync.py`
   - Delete `cron_sync_pending_repos` from `backend/app/workers/main.py`
   - Delete `REPO_CACHE_ROOT`, `REPO_SYNC_INTERVAL_MINUTES` from config
   - Update tests
4. **`feat(db): asyncpg pooler-safe engine config (Neon pgbouncer compat)`**
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

| Package | When | Adds |
|---|---|---|
| **#1 pilot** (this plan) | this week | GitHub App + GitHub Actions + Notion + Linear |
| **#2 MS-stack** | next sprint | Azure DevOps Services (Repos + Pipelines + Boards) + Microsoft Teams via single Entra ID app |
| **#3 Atlassian + Slack** | 2-3 weeks | Jira Cloud + Slack |
| **#4 Long-tail** | on demand | GitLab Cloud, Bitbucket Cloud, ClickUp, Asana, Sentry-as-source, Datadog-as-source |
| **#5 Enterprise tier (broker)** | when first paying enterprise asks | Self-hosted broker for on-prem GHES / GitLab SM / ADO Server. **Paywall.** |

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
