# Ship

Ship is an instruction-first framework for SDLC automation.

Instead of shipping one hardcoded runtime, Ship ships:
- a portable operating model,
- prompts/playbooks for coding agents,
- reference implementations and migration patterns.

Your agent adapts Ship to your real stack (Linear/Jira/GitHub Issues/spreadsheets, any CI, any agent runtime).

## Quick start

1. Open the manual: after `npm run landing:dev`, visit [http://127.0.0.1:3000/docs/getting-started](http://127.0.0.1:3000/docs/getting-started)  
2. Use the built-in prompt builder and hand the generated prompt to your agent  
3. Follow with: [http://127.0.0.1:3000/docs/adoption](http://127.0.0.1:3000/docs/adoption)

Optional helper launcher (from a product repo root):

```bash
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

## Repository structure

| Path | Purpose |
|------|---------|
| `documentation/` | Source markdown for the **manual** (served under `/docs` on the Next.js site). |
| `prompts/` | Reusable prompts and onboarding playbooks. |
| `patterns/` | Org patterns manifest + catalog metadata. |
| `tools/` | **Tools catalog** manifest (`/tools` — integrations: Linear, Actions, Playwright, Cursor Cloud, Chroma, API, …). |
| `workflows/` | **Workflow intents** manifest (`/workflows` — SDLC lane, PR gates, E2E, self-heal, audits). |
| `collections/` | **Curated bundles** manifest (`/collections` — web app, API service, adoption minimum). |
| `backend/` | Agent-facing API (`/search`, `/fetch`, `/feedback`, `/patterns`, …). |
| `cli/` | **`ship` CLI** — HTTP client for that API + `init` to inject agent instructions. |
| `landing/` | Next.js app: marketing UI, **The book** (`/book`), **Patterns** (`/patterns`), **Manual** (`/docs/**`). |
| `scripts/` | Repo maintenance/deployment helper scripts. |
| `examples/` | Reference implementation materials and contribution scaffolds. |

## Local development

### Web (Next.js — marketing + manual + book)

From the **repository root** (where this `README.md` lives):

```bash
npm install
cp landing/.env.example landing/.env.local
# optional: TOGETHER_API_KEY for on-page image generation (Together / FLUX)
npm run landing:dev
```

Then open [http://127.0.0.1:3000](http://127.0.0.1:3000) — **Manual** lives at [/docs](http://127.0.0.1:3000/docs), **The book** at [/book](http://127.0.0.1:3000/book), **Patterns** at [/patterns](http://127.0.0.1:3000/patterns).

Equivalent (runs with `landing/` as cwd):

```bash
cd landing && npm install && npm run dev
```

Do **not** run `npx next dev` from the repo root: there is no `next.config` there, and you will get confusing errors.

### Backend API

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

### Ship CLI

From the repository root (after `npm install`):

```bash
npm run ship -- help
npm run ship -- patterns list
npm run ship -- tools list
npm run ship -- workflows list
npm run ship -- collections list
npm run ship -- docs search "release gates" --top-k 5
```

`tools`, `workflows`, and `collections` read manifests from disk (no API). With the API on another host: `SHIP_API_BASE=https://example.com npm run ship -- patterns list`  
(or pass `--base-url` on any subcommand). `ship init` detects Cursor / `AGENTS.md` / `CLAUDE.md` / `.codex` / Copilot instructions and, after confirmation, writes or appends API usage notes for agents.

## Backend API (and CLI)

Humans and scripts typically use **`npm run ship -- …`** from this repo: **`patterns`**, **`docs`** (search/fetch/feedback) call the HTTP API; **`tools`**, **`workflows`**, **`collections`** read repo manifests directly.

- `GET /patterns` / `GET /patterns/{id}` — curated org patterns (manifest + body); **CLI:** `ship patterns list`, `ship patterns show <id>`
- `POST /search` — vector search over Ship methodology content (local Chroma + OpenAI embeddings); **CLI:** `ship docs search "<query>"`
- `POST /fetch` — full page/file fetch after snippet search; **CLI:** `ship docs fetch <path>`
- `POST /feedback` — create GitHub issue with automatic sensitive-data sanitization; **CLI:** `ship docs feedback …`

## Production container

The root `Dockerfile` builds the Next app from the monorepo (so `/docs` can read `documentation/`). Deploy with the same image you already publish (e.g. Docker Hub + Bunny); set `REPO_ROOT=/app` is the default in the image.

## Important notes

- Ship is **knowledge + methodology** plus a small **HTTP API** and a **local CLI** (\`npm run ship\`) that wraps it — not a hosted proprietary control plane.
- Secrets are never committed; agents should only reference secret names in outputs.
- Breaking migration notes: [documentation/adoption/migration-instruction-first-v0.6.md](documentation/adoption/migration-instruction-first-v0.6.md)

## License

Apache License 2.0 — see [LICENSE](LICENSE).
