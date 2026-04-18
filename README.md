# Ship

Ship is an instruction-first framework for SDLC automation.

Instead of shipping one hardcoded runtime, Ship ships:
- a portable operating model,
- versioned artifacts (patterns, tools, workflows, collections) for coding agents,
- reference implementations and adoption playbooks.

Your agent adapts Ship to your real stack (Linear/Jira/GitHub Issues/spreadsheets, any CI, any agent runtime).

## Quick start

1. Open the manual: after `npm run landing:dev`, visit [http://127.0.0.1:3000/docs/getting-started](http://127.0.0.1:3000/docs/getting-started)  
2. Use the built-in prompt builder and hand the generated prompt to your agent  
3. Follow with: [http://127.0.0.1:3000/docs/adoption](http://127.0.0.1:3000/docs/adoption)

Optional helper launcher (from a product repo root):

```bash
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

### CLI without cloning the full monorepo

1. Install **`@elmundi/ship-cli`** from npm (or run via **`npx @elmundi/ship-cli`** once published); the binary is **`ship`**.
2. **`npx @elmundi/ship-cli pattern list`** (and `tool` / `workflow` / `collection`; plural aliases work) use the **same deployed methodology API** as **`ship search`** and **`ship docs`** (`GET /patterns`, `GET /tools`, …). Set **`SHIP_API_BASE`** to that public URL (defaults to the public methodology host unless overridden).
3. Optional: set **`SHIP_REPO`** or run from this clone to read manifests from disk instead of HTTP.
4. In your product repo, **`npx @elmundi/ship-cli init`** (use **`--dry-run`** first; **`--yes`** for non-interactive installs — see **`ship init help`**).

**`ship search`**, **`ship docs`**, and catalog commands still use the methodology FastAPI (**`SHIP_API_BASE`**).

## Repository structure

| Path | Purpose |
|------|---------|
| `documentation/` | Source markdown for the **manual** and **The book** (served under `/docs` and `/book` on the Next.js site). |
| `artifacts/` | All Ship artifacts in folder-per-artifact layout (`artifacts/<kind>/<id>/ARTIFACT.md` with YAML frontmatter — see [RFC-0005](documentation/rfc/rfc-0005-artifact-folder-spec-v2.md)). Subfolders: `patterns/`, `tools/`, `workflows/`, `collections/`. |
| `backend/` | Agent-facing API (`/search`, `/fetch`, `/feedback`, `/patterns`, …). |
| `cli/` | **`ship` CLI** — one FastAPI client (search, fetch, feedback, catalogs) + optional disk + `init`. |
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
npm run ship -- pattern list
npm run ship -- tool list
npm run ship -- workflow list
npm run ship -- collection list
npm run ship -- search "release gates" --top-k 5
```

`pattern`, `tool`, `workflow`, and `collection` (plural aliases `patterns`, `tools`, …) use the **same FastAPI** as **`ship docs`** when you are not inside a Ship checkout (`SHIP_API_BASE` / `--base-url`). From this repo (or **`SHIP_REPO`**), the same commands read manifests from disk.  
`ship init` detects Cursor / `AGENTS.md` / `CLAUDE.md` / `.codex` / Copilot instructions and, after confirmation, writes or appends API usage notes for agents.

## Backend API (and CLI)

Humans and scripts typically use **`npm run ship -- …`** from this repo or **`npx @elmundi/ship-cli`** elsewhere: one **methodology HTTP API** serves **`ship search`**, **`ship docs`** (fetch markdown by path + feedback), and **`pattern` / `tool` / `workflow` / `collection`** list/show/fetch, or use disk when cwd / **`SHIP_REPO`** is inside this tree.

- `GET /patterns` / `GET /patterns/{id}` — **CLI:** `ship pattern list`, `ship pattern show <id>`; full body via **`ship pattern fetch <id>`** → `POST /fetch` with `{ "kind": "pattern", "id" }`
- `GET /tools`, `GET /tools/{id}`, same for **`/workflows`**, **`/collections`** — **CLI:** `ship tool|workflow|collection list|show|fetch <id>`
- `POST /search` — vector search over Ship methodology content (local Chroma + OpenAI embeddings); **CLI:** `ship search "<query>"`
- `POST /fetch` — repo file by path **`{ "path": "…" }`** (**CLI:** `ship docs fetch <path>`) or catalog entry **`{ "kind": "pattern"|…, "id" }`** (**CLI:** `ship pattern|tool|… fetch <id>`)
- `POST /feedback` — create GitHub issue with automatic sensitive-data sanitization; **CLI:** `ship docs feedback …`

## Production container

The root `Dockerfile` builds the Next app from the monorepo (so `/docs` can read `documentation/`). Deploy with the same image you already publish (e.g. Docker Hub + Bunny); set `REPO_ROOT=/app` is the default in the image.

## Important notes

- Ship is **knowledge + methodology** plus a small **HTTP API** and a **local CLI** (\`npm run ship\`) that wraps it — not a hosted proprietary control plane.
- Secrets are never committed; agents should only reference secret names in outputs.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
