# Ship

Ship is an **instruction-first** framework for SDLC automation: a portable operating model plus versioned artifacts (patterns, tools, workflows, collections) that any coding agent — Cursor, Claude, Codex, Copilot, Aider, and friends — can consume to drive real work in a real repo.

Instead of shipping one hardcoded runtime, Ship ships:
- a **methodology** (see *The book* under [`/book`](https://ship.elmundi.com.ua/book) on the live site, source in [`documentation/framework/`](documentation/framework/))
- **versioned artifacts** under [`artifacts/`](artifacts/) following [RFC-0005](documentation/rfc/rfc-0005-artifact-folder-spec-v2.md) (single `ARTIFACT.md` per item, YAML frontmatter as the source of truth)
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

## Repository structure

| Path | Purpose |
|------|---------|
| [`documentation/`](documentation/) | Source markdown for the **manual** (`/docs/**`), **The book** (`/book`), and the [RFC index](documentation/rfc/). |
| [`artifacts/`](artifacts/) | Every Ship artifact in folder-per-artifact layout (`artifacts/<kind>/<id>/ARTIFACT.md` + YAML frontmatter — see [RFC-0005](documentation/rfc/rfc-0005-artifact-folder-spec-v2.md)). Subfolders: `patterns/`, `tools/`, `workflows/`, `collections/`. |
| [`backend/`](backend/) | Methodology FastAPI: `/search`, `/fetch`, `/feedback`, `/patterns`, `/tools`, `/workflows`, `/collections`, `/telemetry`. |
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
npm run shipctl -- workflow list
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
| `GET /tools` / `/workflows` / `/collections` (+ `/{id}`) | `shipctl tool\|workflow\|collection list\|show <id>` |
| `POST /fetch` `{kind,id[,version]}` | `shipctl pattern\|tool\|workflow\|collection fetch <id>` |
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
- Telemetry is **opt-in** and OFF by default — see [RFC-0003](documentation/rfc/rfc-0003-telemetry-and-feedback.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
