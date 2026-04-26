# Ship console

Operator console for the Ship cloud platform.

This is a separate Next.js app from the marketing landing (`landing/`). It
talks to the FastAPI backend (`backend/`) over the `/v1` API.

Wiring status:

- **Auth** — local email + password, real (`/v1/auth/local/{signup,login}`)
- **Onboarding wizard** — repo-driven, all six steps real:
  inspect repo → create workspace → install workflows (with git commit) →
  configure tracker (encrypted secret) → seed knowledge docs (with git
  commit) → mint CLI PAT
- **Catalog** — real (`/v1/workspaces/{id}/artifacts/{kind}` via the resolver)
- **Integrations / secrets** — real (`/v1/workspaces/{id}/integrations`,
  Fernet-encrypted with `ENCRYPTION_KEY`)
- **Dashboard / daily / workflows / effectiveness / telemetry / etc.** —
  still mock; surfaces fall back to `src/lib/mock/` when there's no backend,
  no session, or no workspace yet.

## Run

From the repo root:

```bash
npm install --prefix console
make dev-console
```

Defaults to **port 3001** so it doesn't collide with the marketing landing on
3000. Open <http://localhost:3001>.

`make dev-console` loads the repo-root `.env`, defaults
`SHIP_API_URL=http://localhost:8100`, and sets `APP_BASE_URL` /
`SHIP_CONSOLE_URL` to `http://localhost:3001` for the Auth0 callback flow.
Run `make dev-backend` in another terminal, or use `make dev-local` from the
repo root to start both local components together.

When `SHIP_API_URL` is unset, the console renders the mock fixtures and
shows a yellow `mock` badge in the sign-in card. With it set, you'll see a
`live` badge and `Reading from /v1` banner on `/catalog`.

For Docker, the `console` service in `docker-compose.yml` already wires
`SHIP_API_URL=http://ship-server:8100` and exposes the console on host port
`3001`. Bring the whole stack up with `docker compose up -d`.

## How auth wiring works

- Login/signup post to `/api/auth/{login,signup}` (plain form posts, not
  React Server Actions). The handler calls `/v1/auth/local/*`, sets a
  httpOnly `ship_session` cookie carrying the JWT, and 303-redirects to `/`.
- Server components / route handlers read the cookie via `getSessionToken`
  and forward it as `Bearer ...` when calling the API.
- Logout is `POST /logout` (POST-only on purpose — a GET handler would be
  silently prefetched by Next from the in-app `<Link>`s in the shell and
  expire the cookie behind the user's back).

## Onboarding wizard

`/onboarding` is a six-step, **repo-driven** flow. Every step calls a real
backend endpoint; structural changes land in the user's repo as a single
named commit they can review.

1. **Repo** — `POST /api/onboard/inspect` →
   `POST /v1/onboarding/inspect` runs `RepoInspector` (clones remote URLs
   shallowly into `/tmp/ship-repos/`, walks local paths in place) and returns
   a `RepoProfile`: detected language, frameworks, package managers, CI,
   tests, README excerpt, suggested workspace identity, and recommended
   workflow IDs. New laptops can hit **Try with a demo repo** which calls
   `POST /v1/onboarding/scaffold-demo-repo` to materialize a tiny Next.js +
   FastAPI fixture under `/tmp/ship-repos/demo-*` and continue from there.
2. **Workspace** — `POST /api/onboard/workspace` → `/v1/workspaces`, with
   the suggested name/slug pre-filled from the profile.
3. **Workflows** — `POST /api/onboard/workflows` →
   `POST /v1/onboarding/install-workflows` runs `WorkflowInstaller`. For each
   approved workflow it writes `.github/workflows/{id}.yml` (a thin GitHub
   Actions stub that calls the Ship CLI) plus the full artifact contract at
   `.ship/workflows/{id}.md`, refreshes `.ship/lock.yaml`, and commits
   everything as `ship: install N workflow(s)`.
4. **Tracker** — `POST /api/onboard/integration` →
   `PUT /v1/workspaces/{id}/integrations/{kind}` (Fernet/AES-128-GCM at
   rest, keyed on `ENCRYPTION_KEY`).
5. **Knowledge** — `POST /api/onboard/knowledge` →
   `POST /v1/onboarding/seed-knowledge` runs `KnowledgeSeeder`. It generates
   three opinionated markdown docs from the profile — `brandbook.md`
   (distilled README + tagline + voice cues), `code-style.md` (formatters,
   linters, language conventions) and `testing.md` (detected frameworks,
   pyramid, sample command) — drops them under `.ship/knowledge/`, and
   commits them as `ship: seed knowledge buckets`. These become the first
   ingestion source for the upcoming knowledge-bucket indexer.
6. **CLI token** — `POST /api/onboard/mint-token` → `/v1/auth/tokens`; the
   plaintext PAT is rendered once with a copy button (the server only stores
   the SHA-256).

A logged-in user with zero workspaces is auto-redirected from `/` into
`?step=repo`. Every step accepts **Skip for now →** so a tire-kicker can
land on the dashboard in two clicks.

### Backend services that power the wizard

- `backend/app/services/repo_inspector.py` — repo resolution + profile.
- `backend/app/services/workflow_installer.py` — file generation + Git
  commit. Configures `user.email=ship@onboarding.local` /
  `user.name=Ship Onboarding` if the target repo has none.
- `backend/app/services/knowledge_seeder.py` — template-driven brandbook /
  code-style / testing docs.

All three operate on the local filesystem visible to `ship-server`. In the
Docker compose dev stack, that means `/tmp` inside the container — the
included `scripts/console-repo-onboarding-smoke.mjs` exercises the
**Demo repo** path so you don't need to mount a workspace volume.

## Encrypted secrets

Set `ENCRYPTION_KEY` in `.env` to the output of
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
in any environment past laptop dev. If unset, the backend logs a loud
warning and derives a key from `JWT_SECRET` so `docker compose up` Just
Works for evaluation.

## Smoke tests

End-to-end signup → workspace create → live catalog screenshot:

```bash
node scripts/console-live-smoke.mjs
# screenshots in output/playwright/live-*.png
```

Full onboarding wizard + integrations CRUD round-trip:

```bash
node scripts/console-onboarding-smoke.mjs
# screenshots in output/playwright/wizard-*.png
```

Repo-driven onboarding (the new six-step flow — scaffolds a demo repo,
installs workflows, seeds knowledge, mints a PAT, and verifies the
catalog/integrations pages show live data):

```bash
node scripts/console-repo-onboarding-smoke.mjs
# screenshots in output/playwright/repo-onboarding/
```

## Routes

| Path | Purpose |
| --- | --- |
| `/` | Operating dashboard — KPIs, yesterday's digest, action items, recent lane runs |
| `/login` | Sign-in screen (no shell chrome) |
| `/onboarding` | First-workspace wizard |
| `/catalog` | Artifact catalog — global + workspace + project layers |
| `/catalog/[id]` | Artifact detail — README, version history, "use in project" |
| `/catalog/pull-requests` | PRs against connected artifact repos, merged from the UI |
| `/knowledge` | Knowledge buckets index |
| `/knowledge/[id]` | Bucket detail — chunk search, pipeline status, doc list |
| `/daily` | Daily digest + retro action-item queue |
| `/workflows` | Workflow lane runs |
| `/effectiveness` | Lead time / throughput / MTTR / retro follow-through |
| `/telemetry` | Live event stream + exporters |
| `/settings` | Workspace settings (general, catalog sources, repos) |
| `/members` | Workspace members + roles |
| `/integrations` | Linear, GitHub, Slack, OTel, webhooks, S3 |
| `/preview/empty` | Design preview: every empty state on one page |

## Theming

Tailwind v3 with the same brand palette as `landing/`:
`ink`, `mist`, `coral`, `aqua`, `lilac`, `sun`. Fonts are `Plus Jakarta Sans`
(`--font-heading`) and `DM Sans` (`--font-dm`) loaded through `next/font`.
