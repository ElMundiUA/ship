# Ship backend API

Instruction-first companion API for agents — and the foundation of the Ship cloud platform (RFC-0006).

## Endpoints

### Methodology API (unauthenticated, kept stable for the released CLI)

`npx @elmundi/ship-cli pattern list` (or `npm run shipctl -- pattern list` from this repo) uses **`SHIP_API_BASE`** to call:

- `GET /patterns` — list curated org patterns scanned from `artifacts/patterns/<id>/ARTIFACT.md` (frontmatter only).
- `GET /patterns/{id}` — one pattern plus full `ARTIFACT.md` (frontmatter + body).
- `GET /tools`, `GET /tools/{id}` — tools index + body.
- `GET /collections`, `GET /collections/{id}` — collections index + body.
- `POST /search` — vector search over methodology files (`documentation/`, `artifacts/**/ARTIFACT.md`, `README.md`).
- `POST /fetch` — fetch full content for a selected file or catalog entry.
- `POST /feedback` — create GitHub issues in the Ship repo after sanitizing sensitive fragments.
- `POST /telemetry` — opt-in adoption events (RFC-0003).

### Cloud platform v1 (multi-tenant, authenticated; RFC-0006)

- `GET /v1/health` — liveness + Postgres round-trip.
- `GET /v1/workspaces` — workspaces the caller belongs to.
- `POST /v1/workspaces` — create a workspace under the caller's org.
- `GET /v1/workspaces/{id}` — fetch a single workspace (404 unless the caller is a member).
- `POST /v1/integrations/github/install/start` + callback — kick off the
  GitHub App install flow that backs the WOW onboarding (no repo clones).
- `GET /v1/workspaces/{id}/repos/available` /
  `POST /v1/workspaces/{id}/repos/activate` — list and pick repos visible
  to the GitHub App installation; activation auto-seeds the five default
  pipelines via `seed_default_pipelines`.

Auth: `Authorization: Bearer <token>` where `<token>` is either a session JWT or a personal access token prefixed `ship_pat_`.

The API version is reported by `GET /openapi.json` and matches the canonical Ship release in [`/VERSION`](../VERSION) (kept in sync by `scripts/version.mjs`).

## Run the cloud platform locally (one command)

The lean backend stack (Postgres+pgvector, MinIO, API server, console) lives behind a single `docker-compose.yml` at the repo root.

```bash
cp .env.example .env       # defaults are fine for local
docker compose up --build
```

The ARQ worker + Redis are behind a `worker` profile so the default `up` matches the cloud SaaS topology (no worker, no Redis). Spin them up locally only when you need background jobs:

```bash
docker compose --profile worker up --build
```

When everything is healthy:

- API: <http://localhost:8100/v1/health>
- MinIO console: <http://localhost:9001> (creds from `.env`)
- Postgres: `localhost:5433` (user/pass `ship`/`ship`)

The first boot runs Alembic migrations against the empty database; subsequent boots are no-ops.

## Run the API directly (no Docker)

```bash
. .venv/bin/activate
pip install -r requirements-backend.txt

# Migrations (Postgres must already be reachable at DATABASE_URL)
alembic -c backend/alembic.ini upgrade head

# API
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100

# Worker (separate shell)
arq backend.app.workers.main.WorkerSettings
```

## Run against shared dev infrastructure

For the fast laptop loop, keep the backend and console local while reusing the
shared dev Postgres/Auth0/S3 configuration from the repo-root `.env`.

```bash
make dev-migrate   # optional: applies Alembic to the configured dev database
make dev-backend   # FastAPI on http://127.0.0.1:8100
```

The direct dev targets run under `.venv`, load `.env`, and export
`SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS=true` so `SHIP_AUTH_MODE=auth0` can use
`SHIP_PUBLIC_URL=http://localhost:8100` and
`SHIP_CONSOLE_URL=http://localhost:3001`. The flag is not set by the Docker or
production targets, so deployed Auth0 environments still fail fast if callback
origins accidentally point at localhost.

## Required env

Methodology API:

- `OPENAI_API_KEY` — embeddings for `/search`
- `GITHUB_TOKEN` — issue creation for `/feedback`

Cloud platform (RFC-0006) — see `.env.example` for the full list:

- `DATABASE_URL` — `postgresql+asyncpg://...` (Neon-pooled URL in SaaS)
- `REDIS_URL` — only required when running the optional `--profile worker` stack; cloud SaaS leaves it unset
- `S3_*` — object storage for document blobs
- `JWT_SECRET` — sign session tokens; must be a long random string in production
- `ENCRYPTION_KEY` — 32-byte urlsafe base64; required to store integration secrets

Optional:

- `OPENAI_EMBED_MODEL` (default `text-embedding-3-small`)
- `SHIP_FEEDBACK_REPO` (default `ElMundiUA/ship`)
- `FORCE_REINDEX=true` to rebuild the legacy Chroma vector index on startup
- `ALEMBIC_DATABASE_URL` to override the sync URL Alembic uses
- `SHIP_ENABLE_PARTIAL_TRACKERS` (default `false`) — when `false` (production), tracker picker shows only Linear and GitHub Issues; partial integrations (Notion, Jira, Asana, ClickUp, Monday, Spreadsheet) appear as "Coming soon" (disabled). Set to `true` or `1` to reveal all options for testing.

## Operator notes — repo-driven onboarding

The `/v1/onboarding/*` endpoints shell out to `git` to clone, stage, and
commit changes inside the user's repository, so the API process needs:

- **`git` on PATH** — already present in the official `python:3.13-slim`
  base image we ship and in the `ship-server` Docker image.
- **Filesystem access to the target repo** — for local paths the API
  container must be able to `read/write` the directory; for remote URLs the
  inspector clones into `/tmp/ship-repos/`, so make sure that directory is
  writable (the container runs as root by default and the path is on the
  ephemeral container fs).
- **Outbound network** — only when a wizard run targets a remote URL
  (HTTPS clone). For air-gapped environments, point the wizard at a local
  path that's already on disk.

Commits are authored as `Ship Onboarding <ship@onboarding.local>` when the
target repo has no local `user.email`/`user.name` configured; otherwise the
existing identity is preserved. Every wizard mutation is recorded in
`audit_log` with `actor_user_id`, the workspace ID, and a small JSON
payload describing what was installed.

## Tests

```bash
pytest backend/tests
```

Tests that depend on Postgres (the new `/v1/*` suite) automatically **skip** when no database is reachable. To run them, point `TEST_DATABASE_URL` at the local stack:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://ship:ship@localhost:5433/ship pytest backend/tests
```
