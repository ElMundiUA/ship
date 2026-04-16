# Chroma (local vector index)

**Role in Ship:** powers **`POST /search`** in the bundled FastAPI service — embeddings over `documentation/`, `prompts/`, and `README.md`, persisted under `backend/.chroma/`.

## Operator notes

- Requires **`OPENAI_API_KEY`** on the server for embedding generation; index rebuilds when content fingerprints change (or when `FORCE_REINDEX=true`).
- Not a multi-tenant hosted vector DB — **local-first** for methodology search on a laptop or single VM.

## Read next

- [Backend API](/docs/tools/backend-api) — `/search`, `/fetch`, `/feedback`, `/patterns` (from the Ship repo use `npm run ship -- docs …` and `npm run ship -- patterns …`).
- Ship CLI: `ship search …` from the repo root (see root `package.json` script `ship`).
