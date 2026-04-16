# Ship backend API

Instruction-first companion API for agents.

## Endpoints

From any machine, `npm run ship -- pattern list` uses **`SHIP_API_BASE`** (`GET /patterns`, …), or reads from disk inside the monorepo (or with `SHIP_REPO`).

- `GET /patterns` — list curated org patterns from `patterns/manifest.json` (metadata only).
- `GET /patterns/{id}` — one pattern plus full markdown body.
- `GET /tools`, `GET /tools/{id}` — tools manifest + body (same as CLI `ship tool …`).
- `GET /workflows`, `GET /workflows/{id}` — workflows manifest + body.
- `GET /collections`, `GET /collections/{id}` — collections manifest + body.
- `POST /search` — vector search over methodology files (`documentation/`, `prompts/`, `README.md`) using local Chroma + OpenAI embeddings.
- `POST /fetch` — fetch full content for a selected markdown/text file.
- `POST /feedback` — create GitHub issues in Ship repo after sanitizing sensitive fragments.

## Run locally

```bash
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

## Required env

- `OPENAI_API_KEY` — embeddings for `/search`
- `GITHUB_TOKEN` — issue creation for `/feedback`

Optional:

- `OPENAI_EMBED_MODEL` (default `text-embedding-3-small`)
- `SHIP_FEEDBACK_REPO` (default `ElMundiUA/ship`)
- `FORCE_REINDEX=true` to rebuild vector index on startup
