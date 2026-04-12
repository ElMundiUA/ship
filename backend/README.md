# Ship backend API

Instruction-first companion API for agents.

## Endpoints

From the Ship monorepo, prefer the CLI: `npm run ship -- patterns list` and `npm run ship -- patterns show <id>` (same payloads as below).

- `GET /patterns` — list curated org patterns from `patterns/manifest.json` (metadata only).
- `GET /patterns/{id}` — one pattern plus full markdown body.
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
