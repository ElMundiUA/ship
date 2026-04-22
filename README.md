# Ship

Ship is an **instruction-first** framework for SDLC automation: a portable operating model plus versioned artifacts (patterns, tools, collections) that any coding agent — Cursor, Claude, Codex, Copilot, Aider, and friends — can consume to drive real work in a real repo.

Instead of shipping one hardcoded runtime, Ship ships:
- a **methodology** (see *The book* under [`/book`](https://ship.elmundi.com.ua/book) on the live site, source in [`documentation/framework/`](documentation/framework/))
- **versioned artifacts** under [`artifacts/`](artifacts/) following [RFC-0005](documentation/protocol/rfc-0005-artifact-folder-spec-v2.md) (single `ARTIFACT.md` per item, YAML frontmatter as the source of truth)
- a **CLI** ([`@elmundi/ship-cli`](cli/), binary `shipctl`) that talks to a thin **methodology HTTP API** ([`backend/`](backend/))
- a **Next.js site** ([`landing/`](landing/)) that serves the manual, *The book*, the artifact catalogs, the use-case pages, and the marketing surface

Your agent adapts Ship to your real stack — Linear / Jira / GitHub Issues / spreadsheets, any CI, any agent runtime.

## Try it in 60 seconds

In any product repo:

```bash
npx @elmundi/ship-cli init --dry-run
```

`shipctl init` detects what you already have (`.cursor/`, `AGENTS.md`, `CLAUDE.md`, `.codex/`, Copilot instructions, Aider, Cline, Continue, Windsurf, Zed, Gemini, Opencode) and **plans only the injections that fit your tree**. Re-run without `--dry-run` (or with `--yes` for non-interactive) to apply.

Full walkthrough on the site: <https://ship.elmundi.com.ua/docs/getting-started>.

## Stack coverage matrix

Every Ship deployment is the same loop with three swappable roles ([RFC-0004](documentation/protocol/rfc-0004-adapters.md)):

- **Tracker** (a.k.a. *orchestrator* / system of record) — Linear, Jira, GitHub Issues, Notion, …
- **Scheduler** (cron + runner that wakes the loop) — GitHub Actions, GitLab CI, CircleCI, Jenkins, self-hosted cron, …
- **Agent** (the coding agent that produces the diff) — Cursor, Cursor Cloud, Claude Code, Codex CLI, GitHub Copilot, Aider, Cline, Continue, Windsurf, Zed, Gemini, Opencode, …

The matrix below is the **smoke-test surface** we want green for every release. Status legend:

- **validated** — wired by an artifact under [`artifacts/`](artifacts/) **and** exercised by an end-to-end smoke run (real ticket → real PR).
- **partial** — adapter / agent-rules artifact exists but no automated smoke run yet.
- **planned** — on the roadmap, no artifact yet (community PRs welcome).
- **n/a** — combination intentionally unsupported (e.g. agent has no CI-friendly runner).

### Per-role support

| Tracker (orchestrator) | Adapter artifact | Status |
|---|---|---|
| Linear | [`tools/linear`](artifacts/tools/linear/) | validated |
| GitHub Issues | [`tools/tracker-contract`](artifacts/tools/tracker-contract/) | partial |
| Jira | [`tools/tracker-contract`](artifacts/tools/tracker-contract/) | partial |
| Notion | — | planned |
| Asana / ClickUp / Monday | — | planned |
| Spreadsheet (Sheets / CSV) | [`tools/tracker-contract`](artifacts/tools/tracker-contract/) | partial |

| Scheduler | Adapter artifact | Status |
|---|---|---|
| GitHub Actions | [`tools/github-actions`](artifacts/tools/github-actions/) | validated |
| GitLab CI | — | planned |
| CircleCI | — | planned |
| Jenkins | — | planned |
| Buildkite | — | planned |
| Self-hosted cron / systemd timer | — | partial |
| Temporal / Airflow | — | planned |

| Agent | Rules collection | Status |
|---|---|---|
| Cursor (desktop) | [`collections/agent-rules-cursor`](artifacts/collections/agent-rules-cursor/) | validated |
| Cursor Cloud | [`collections/agent-rules-cursor-cloud`](artifacts/collections/agent-rules-cursor-cloud/) + [`tools/cursor-cloud-agent`](artifacts/tools/cursor-cloud-agent/) | validated |
| Claude Code | [`collections/agent-rules-claude`](artifacts/collections/agent-rules-claude/) / [`agent-rules-claude-md`](artifacts/collections/agent-rules-claude-md/) | partial |
| Codex CLI | [`collections/agent-rules-codex`](artifacts/collections/agent-rules-codex/) | partial |
| GitHub Copilot (workspace / coding agent) | [`collections/agent-rules-copilot`](artifacts/collections/agent-rules-copilot/) | partial |
| Aider | [`collections/agent-rules-aider`](artifacts/collections/agent-rules-aider/) | partial |
| Cline | [`collections/agent-rules-cline`](artifacts/collections/agent-rules-cline/) | partial |
| Continue | [`collections/agent-rules-continue`](artifacts/collections/agent-rules-continue/) | partial |
| Windsurf | [`collections/agent-rules-windsurf`](artifacts/collections/agent-rules-windsurf/) | partial |
| Zed | [`collections/agent-rules-zed`](artifacts/collections/agent-rules-zed/) | partial |
| Gemini CLI | [`collections/agent-rules-gemini`](artifacts/collections/agent-rules-gemini/) | partial |
| Opencode | [`collections/agent-rules-opencode`](artifacts/collections/agent-rules-opencode/) | partial |
| Generic `AGENTS.md` | [`collections/agent-rules-agents-md`](artifacts/collections/agent-rules-agents-md/) | partial |

### End-to-end combinations (Tracker × Agent × Scheduler)

The full Cartesian product is ~14 × 7 × 7 ≈ **686 combos**. Realistically we test the tier-1 grid below (~30) and let the rest fall back to per-role coverage.

| # | Tracker | Agent | Scheduler | Status | Reference |
|---|---------|-------|-----------|--------|-----------|
| 1  | Linear         | Cursor             | GitHub Actions   | validated | [`patterns/role-developer`](artifacts/patterns/role-developer/) + `.ship/config.yml` lane |
| 2  | Linear         | Cursor Cloud       | GitHub Actions   | validated | [`tools/cursor-cloud-agent`](artifacts/tools/cursor-cloud-agent/) |
| 3  | Linear         | Claude Code        | GitHub Actions   | partial   | [`collections/agent-rules-claude`](artifacts/collections/agent-rules-claude/) |
| 4  | Linear         | Codex CLI          | GitHub Actions   | partial   | [`collections/agent-rules-codex`](artifacts/collections/agent-rules-codex/) |
| 5  | Linear         | GitHub Copilot     | GitHub Actions   | partial   | [`collections/agent-rules-copilot`](artifacts/collections/agent-rules-copilot/) |
| 6  | Linear         | Aider              | GitHub Actions   | partial   | [`collections/agent-rules-aider`](artifacts/collections/agent-rules-aider/) |
| 7  | Linear         | Gemini CLI         | GitHub Actions   | partial   | [`collections/agent-rules-gemini`](artifacts/collections/agent-rules-gemini/) |
| 8  | Linear         | Cursor             | GitLab CI        | planned   | scheduler adapter pending |
| 9  | Linear         | Cursor             | CircleCI         | planned   | scheduler adapter pending |
| 10 | Linear         | Cursor             | Jenkins          | planned   | scheduler adapter pending |
| 11 | Linear         | Cursor             | Self-hosted cron | partial   | shape-only via `shipctl` |
| 12 | GitHub Issues  | Cursor             | GitHub Actions   | partial   | tracker adapter pending |
| 13 | GitHub Issues  | Cursor Cloud       | GitHub Actions   | partial   | tracker adapter pending |
| 14 | GitHub Issues  | Claude Code        | GitHub Actions   | partial   | tracker + agent partial |
| 15 | GitHub Issues  | GitHub Copilot     | GitHub Actions   | partial   | tracker + agent partial |
| 16 | GitHub Issues  | Codex CLI          | GitHub Actions   | partial   | tracker + agent partial |
| 17 | Jira           | Cursor             | GitHub Actions   | partial   | tracker adapter pending |
| 18 | Jira           | Cursor Cloud       | GitHub Actions   | partial   | tracker adapter pending |
| 19 | Jira           | Claude Code        | GitHub Actions   | partial   | tracker adapter pending |
| 20 | Jira           | Codex CLI          | Jenkins          | planned   | both adapters pending |
| 21 | Jira           | Aider              | GitLab CI        | planned   | both adapters pending |
| 22 | Notion         | Cursor             | GitHub Actions   | planned   | tracker adapter not started |
| 23 | Notion         | Claude Code        | GitHub Actions   | planned   | tracker adapter not started |
| 24 | Notion         | Cursor Cloud       | GitHub Actions   | planned   | tracker adapter not started |
| 25 | Notion         | Codex CLI          | GitHub Actions   | planned   | tracker adapter not started |
| 26 | Asana          | Cursor             | GitHub Actions   | planned   | tracker adapter not started |
| 27 | ClickUp        | Cursor             | GitHub Actions   | planned   | tracker adapter not started |
| 28 | Spreadsheet    | Cursor             | GitHub Actions   | partial   | tracker-contract shape only |
| 29 | Spreadsheet    | Aider              | Self-hosted cron | partial   | tracker-contract + cron shape |
| 30 | Linear         | Windsurf / Cline / Continue / Zed / Opencode | GitHub Actions | partial | per-agent rules only |

> A row marked **partial** means the artifacts exist and the loop *should* work, but no smoke run has gone green for that exact combination this release. Treat **partial** rows as the queue for the next coverage push — see the automation plan on the PR that introduced this matrix.

## Repository structure

| Path | Purpose |
|------|---------|
| [`documentation/`](documentation/) | Source markdown for the **manual** (`/docs/**`), **The book** (`/book`), and the [RFC index](documentation/protocol/). |
| [`artifacts/`](artifacts/) | Every Ship artifact in folder-per-artifact layout (`artifacts/<kind>/<id>/ARTIFACT.md` + YAML frontmatter — see [RFC-0005](documentation/protocol/rfc-0005-artifact-folder-spec-v2.md)). Subfolders: `patterns/`, `tools/`, `collections/`. |
| [`backend/`](backend/) | Methodology FastAPI: `/search`, `/fetch`, `/feedback`, `/patterns`, `/tools`, `/collections`, `/telemetry`. |
| [`cli/`](cli/) | `@elmundi/ship-cli` (binary `shipctl`) — search, fetch, feedback, catalogs, `init`, `sync`, `verify`, `doctor`, `telemetry`, `feedback`, `new`, `bootstrap`. |
| [`landing/`](landing/) | Next.js app: marketing, **manual** (`/docs/**`), **The book** (`/book`), **Patterns** (`/patterns`), **Use cases** (`/use-cases`), generated PDF (`/book.pdf`). |
| [`scripts/`](scripts/) | Repo maintenance (`version.mjs`, `restamp_artifact_shas.py`, `ship_artifact_check.py`, Bunny / Docker helpers). |
| [`deploy/`](deploy/) | Edge nginx config for the production image. |
| [`VERSION`](VERSION) | Single-line semver — the canonical Ship release version. See [Versioning](#versioning--releases). |

## Local development

This is a small monorepo. From the **repo root**:

```bash
npm install
```

### Web (Next.js — marketing + manual + book)

```bash
cp landing/.env.example landing/.env.local        # optional: TOGETHER_API_KEY for image gen
npm run landing:dev
```

Then open <http://127.0.0.1:3000>: **manual** at [/docs](http://127.0.0.1:3000/docs), **book** at [/book](http://127.0.0.1:3000/book), **patterns** at [/patterns](http://127.0.0.1:3000/patterns), **use cases** at [/use-cases](http://127.0.0.1:3000/use-cases).

> Do **not** run `npx next dev` from the repo root — there is no `next.config` there. Always go through `npm run landing:dev` (or `cd landing && npm run dev`).

Build the downloadable PDF of *The book* (output: `landing/public/book.pdf`):

```bash
npm run book:pdf
```

### Backend API

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

Tests:

```bash
pytest backend/tests -q
```

### CLI (`shipctl`)

From the repo root:

```bash
npm run shipctl -- help                 # full usage
npm run shipctl -- pattern list         # plural alias `patterns` also works
npm run shipctl -- tool list
npm run shipctl -- collection list
npm run shipctl -- search "release gates" --top-k 5
npm run shipctl -- --version
```

In this repo (or with `SHIP_REPO=/path/to/ship`), the catalog commands read `artifacts/**/ARTIFACT.md` directly off disk. Outside it, they hit the methodology API at `SHIP_API_BASE` (defaults to the public host). `shipctl search` and `shipctl docs` always go through HTTP.

CLI tests:

```bash
npm test --prefix cli
```

## Backend API ↔ CLI quick reference

One HTTP API serves both humans (via the live site) and agents (via the CLI). All catalog and doc bodies are returned as full `ARTIFACT.md` files (frontmatter + content).

| HTTP | `shipctl` |
|---|---|
| `GET /patterns`, `GET /patterns/{id}` | `shipctl pattern list`, `shipctl pattern show <id>` |
| `GET /tools` / `/collections` (+ `/{id}`) | `shipctl tool\|collection list\|show <id>` |
| `POST /fetch` `{kind,id[,version]}` | `shipctl pattern\|tool\|collection fetch <id>` |
| `POST /fetch` `{path}` | `shipctl docs fetch <path>` |
| `POST /search` | `shipctl search "<query>"` |
| `POST /feedback` | `shipctl docs feedback …` / `shipctl feedback submit …` |
| `POST /telemetry` | `shipctl telemetry flush` (opt-in, see RFC-0003) |

## Versioning & releases

There is exactly one version, in [`VERSION`](VERSION) at the repo root. Every component (`package.json`, `landing/package.json`, `cli/package.json`, `backend/app/main.py`’s `FastAPI(version=…)`) is kept in lockstep with it by [`scripts/version.mjs`](scripts/version.mjs).

```bash
npm run version:show       # print the canonical version
npm run version:check      # CI guard — fails if any target drifted
npm run version:sync       # rewrite every target back to VERSION
npm run version:bump -- patch   # 0.10.0 → 0.10.1, syncs everything
npm run version:bump -- minor   # 0.10.0 → 0.11.0
npm run version:bump -- 1.0.0   # explicit value
```

The `.github/workflows/version-check.yml` workflow runs `version:check` on every PR that touches a version-bearing file, so drift cannot be merged.

### Release recipe

```bash
npm run version:bump -- minor                   # prints the new version (e.g. 0.11.0)
git commit -am "release v$(cat VERSION)"
git tag "v$(cat VERSION)"
git push --follow-tags
```

The `v<x.y.z>` tag triggers `.github/workflows/npm-publish-cli.yml`, which re-checks version alignment and publishes `@elmundi/ship-cli` to npm. The legacy `cli-v<x.y.z>` tag is still accepted for emergency CLI-only patches.

## Production container

The root [`Dockerfile`](Dockerfile) builds the Next app with `REPO_ROOT=/app`, so `/docs` and `/book` can read `documentation/` and `artifacts/` straight from the image. Deploy with the same image you already publish to Docker Hub + Bunny.

## Important notes

- Ship is **knowledge + methodology** plus a small **HTTP API** and a **local CLI** — not a hosted proprietary control plane.
- Secrets are never committed; agents only reference secret *names* in outputs.
- Telemetry is **opt-in** and OFF by default — see [RFC-0003](documentation/protocol/rfc-0003-telemetry-and-feedback.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
