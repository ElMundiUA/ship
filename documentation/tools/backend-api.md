# Backend API

Ship now includes a lightweight backend for agent workflows: semantic search, file fetch, retro feedback, and **pattern metadata** from the root manifest.

## Purpose

- Give humans and agents semantic access to framework knowledge (`/search` + `/fetch`) and a stable pattern index.
- Collect operational retro feedback into Ship backlog safely (`/feedback`).
- Stay local-first: no external vector database.

## CLI (patterns and docs)

From the **Ship** repository root, the **`ship`** CLI wraps the same HTTP API (defaults to `http://127.0.0.1:8100`; override with `--base-url` or `SHIP_API_BASE`):

```bash
npm run ship -- patterns list
npm run ship -- patterns show catalog-a1-intake
npm run ship -- docs search "intake idempotency" --top-k 6
npm run ship -- patterns list --json
```

Run `npm run ship -- help` for all subcommands. Use `--json` in scripts for stable parsing.

## CLI (tools, workflows, collections)

These read **`tools/manifest.json`**, **`workflows/manifest.json`**, and **`collections/manifest.json`** from disk — **no HTTP server**. Run inside the Ship clone, or set **`SHIP_REPO`** to the monorepo root.

```bash
npm run ship -- tools list
npm run ship -- tools show playwright
npm run ship -- workflows list
npm run ship -- workflows show pr-and-ci-gate
npm run ship -- collections list
npm run ship -- collections show web-application
```

## Endpoints (HTTP)

Agents, CI, and other runtimes may call these directly; humans usually use the CLI above.

### `GET /patterns`

Returns metadata for every entry in `patterns/manifest.json` at the repository root (no file bodies).

Response shape:

```json
{
  "version": 1,
  "description": "...",
  "patterns": [
    {
      "id": "catalog-a1-intake",
      "title": "Structured intake",
      "summary": "...",
      "path": "prompts/catalog/A1-intake.md",
      "tags": ["intake", "labels"],
      "group": "lanes"
    }
  ]
}
```

### `GET /patterns/{pattern_id}`

Returns the same fields as one list item, plus a `content` string with the full markdown file (path must match the manifest and stay inside the repo).

Example (same as `npm run ship -- patterns show catalog-a1-intake --json` without `--json`):

```bash
curl -sS "http://127.0.0.1:8100/patterns/catalog-a1-intake"
```

### `POST /search`

Vector search over:
- `documentation/**/*.md`
- `prompts/**/*.md`
- `README.md`

Uses local Chroma persistence (`backend/.chroma/`) and OpenAI embeddings.

Request:

```json
{
  "query": "qa automation handoff",
  "top_k": 8
}
```

Response returns chunk snippets, path, chunk index, and vector distance.

### `POST /fetch`

Returns full markdown/text content for a file chosen after search.

Request:

```json
{
  "path": "documentation/adoption/delivery-quality-and-release-process.md"
}
```

### `POST /feedback`

Creates an issue in Ship repo from daily retro insights.

Before issue creation, payload is scanned and sanitized:
- emails,
- common API token/key formats,
- simple `password/token/secret` key-value patterns.

If sensitive fragments are detected, they are rewritten into generalized placeholders before sending to GitHub.

## Local run

```bash
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

## Required environment variables

- `OPENAI_API_KEY` for `/search`
- `GITHUB_TOKEN` for `/feedback`

Optional:
- `SHIP_FEEDBACK_REPO` (default: `ElMundiUA/ship`)
- `OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `FORCE_REINDEX=true` (force rebuild of vector index)
