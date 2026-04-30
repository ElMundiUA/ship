# Ship

Ship is a product delivery workspace for teams adopting AI-assisted engineering. It connects repos, trackers, knowledge, automations, and agent rules so product owners can see what moved, what is blocked, who needs to decide, and which evidence backs the work.

The product keeps the book's core posture: humans own intent, automation stays bounded, evidence beats opinion, and vendors are plugs rather than the story.

## What is in this repo

- **Console** ([`console/`](console/)) — Next.js workspace UI on port 3001. It talks to the FastAPI `/v1` API and renders workspace home, onboarding, repos, Inbox, knowledge, integrations, members, policies, audit, and per-repo pages.
- **Backend** ([`backend/`](backend/)) — FastAPI service. It serves the public methodology/catalog API plus the `/v1` cloud platform: workspaces, repos, GitHub App, trackers, dashboard, Inbox, knowledge, pipelines, policies, secrets, audit, and chat.
- **Landing** ([`landing/`](landing/)) — marketing site, manual, book route, use cases, catalog pages, blog, and docs UI.
- **Manual** ([`documentation/`](documentation/)) — source Markdown for `/docs/**`. The book content is separate under `landing/content/book.md`.
- **Artifacts** ([`artifacts/`](artifacts/)) — versioned patterns, tools, and collections consumed by the CLI, agents, catalog, and docs.
- **CLI** ([`cli/`](cli/)) — `@elmundi/ship-cli` / `shipctl`, the developer workbench for local setup, sync, verify, config, and artifact commands.
- **E2E** ([`e2e/`](e2e/)) — Playwright coverage for public shell, console journeys, sandbox repos, GitHub App, dashboard, knowledge, integrations, and product tours.

## Product entry points

For readers and product owners:

- Live docs start at `/getting-started` and `/docs`.
- The console starts with a workspace, repo activation, tracker binding, knowledge, and Inbox.
- The book explains the philosophy and is intentionally not the quick-start path.

For developers and platform teams:

- Use [`cli/README.md`](cli/README.md) for `shipctl`.
- Use [`documentation/configuration.md`](documentation/configuration.md) for `.ship/` and config files.
- Use [`backend/app/api/v1/router.py`](backend/app/api/v1/router.py) as the high-level API map.

## Local development

Install workspace dependencies from the repo root:

```bash
npm install
```

### Landing site

```bash
cp landing/.env.example landing/.env.local
npm run landing:dev
```

Open <http://127.0.0.1:3000>. Do not start a second landing dev server on port 3000.

Build:

```bash
npm run landing:build
```

### Backend

Run Python inside `.venv`:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

Tests:

```bash
. .venv/bin/activate
pytest backend/tests -q
```

### Console

The console uses port 3001.

```bash
npm install --prefix console
make dev-console
```

For backend + console together:

```bash
make dev-local
```

Do not run a second console dev server while port 3001 is already served by Next.js or Docker Compose.

### CLI

```bash
npm run shipctl -- help
npm run shipctl -- doctor
npm run shipctl -- verify
npm run shipctl -- pattern list
npm test --prefix cli
```

Inside this repo the catalog commands can read `artifacts/**/ARTIFACT.md` from disk. Outside it, `shipctl` uses the configured Ship API.

## Backend API surfaces

The unversioned methodology API powers the catalog and CLI:

| HTTP | CLI |
| --- | --- |
| `GET /patterns`, `GET /tools`, `GET /collections` | `shipctl pattern|tool|collection list` |
| `POST /fetch` | `shipctl pattern|tool|collection fetch`, `shipctl docs fetch` |
| `POST /search` | `shipctl search` |
| `POST /feedback` | `shipctl feedback submit` |
| `POST /telemetry` | `shipctl telemetry flush` |

The `/v1` API powers the console. The top-level include list lives in [`backend/app/api/v1/router.py`](backend/app/api/v1/router.py).

## Versioning

The canonical version is [`VERSION`](VERSION). Keep package versions and backend app version in sync with:

```bash
npm run version:show
npm run version:check
npm run version:sync
```

Release tags use `v<x.y.z>` and trigger the CLI publish workflow.

## Tracker support matrix

| Tracker | Product Owner | Developer | Delivery Lead | Security Officer |
|---------|---------------|-----------|----------------|------------------|
| Linear | validated | validated | validated | validated |
| GitHub Issues | validated | validated | validated | validated |
| Notion | planned | planned | planned | planned |
| Jira | hidden | hidden | hidden | hidden |
| Asana | planned | planned | planned | planned |
| ClickUp | planned | planned | planned | planned |
| Monday | planned | planned | planned | planned |
| Spreadsheet | hidden | hidden | hidden | hidden |

**Status definitions:**
- **validated** — integration is tested end-to-end in closed beta; read, comment, and state sync work bidirectionally.
- **partial** — implementation exists but integration is incomplete or feature-limited.
- **planned** — integration is on the roadmap and not yet implemented.
- **hidden** — implementation exists in code but is behind a feature flag (`SHIP_ENABLE_PARTIAL_TRACKERS`) and not user-selectable in production.

**Closed beta scope:** Ship currently supports Linear and GitHub Issues for production use. Other trackers are behind a feature flag and will land post-beta. The matrix above describes the support we are building toward.

## Production container

The root [`Dockerfile`](Dockerfile) builds the landing app and includes repo content needed by `/docs`, `/book`, and catalog pages. `docker-compose.yml` runs backend and console for the local/dev platform stack; the public landing app is not a Compose service.
