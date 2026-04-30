# E13 — Rip out Chroma, unify on pgvector

**Priority:** P2
**Effort:** M (~4–6 days)
**Owner:** TBD

## Goal

Remove the ChromaDB dependency from the backend entirely. All vector search runs on Postgres + pgvector, which the cloud platform already uses for buckets / articles / agent memory. Two vector stores in one process is one too many.

## Why

Today there are **two** vector backends:

- **ChromaDB** — used only by the legacy unauthenticated methodology API in `backend/app/main.py`: powers `/search` and `/fetch` over `documentation/`, `artifacts/**/ARTIFACT.md`, `README.md`. Persistent client at `backend/.chroma/`.
- **pgvector** — used by the multi-tenant cloud platform (`/v1/*`): `BucketSummary.embedding`, agent memory, knowledge articles. Postgres image is already `pgvector/pgvector:pg16`.

Operationally that's:
- two embedding pipelines (Chroma + the OpenAI-backed pgvector one);
- two manifests, two on-disk volumes, two failure modes;
- a Docker volume to track for backups (`backend/.chroma`);
- extra startup time at every container boot.

For closed beta the cloud-platform path is the real surface. The methodology API still has to work for the released CLI, but it can ride on pgvector with a small dedicated table.

## Tasks

### T01 — Audit all Chroma touchpoints **[S]**

- `rg "chromadb|\.chroma|CHROMA_" backend/` — list every reference.
- Currently:
  - `backend/app/main.py` — index lifecycle, `/search`, `/fetch` route handlers.
  - `backend/.chroma/` — persisted index dir.
  - `requirements-backend.txt` — `chromadb` pin.
  - `Dockerfile` / `deploy/backend/Dockerfile` — `.chroma/` volume.
  - `docker-compose.yml` — any volume mount.
- Document the audit in this file as a checklist.

**Acceptance:** complete reference list, no surprises later.

### T02 — Design the replacement table **[S]**

- New table `methodology_chunks` (or reuse the existing bucket model with a synthetic "methodology" workspace_id).
- Columns: `id`, `path`, `chunk_idx`, `body`, `content_sha`, `embedding vector(1536)`, `kind` (`doc | artifact | readme`), `slug`.
- HNSW index on `embedding`; unique on `(path, chunk_idx)` for idempotent re-index.
- Add Alembic migration: `0044_methodology_chunks.py`.

**Acceptance:** migration applies cleanly forward and backward; pgvector index works.

### T03 — Re-implement the indexer **[M]**

- New module: `backend/app/services/methodology_index.py`.
- Walks `documentation/`, `artifacts/**/ARTIFACT.md`, `README.md`.
- Chunks the same way Chroma did (preserve current chunk size).
- Embeds via the same OpenAI client used in `services/agent/embedding.py`.
- Idempotent: `content_sha` skip already-indexed chunks.
- Run on backend startup if a manifest is stale (port the manifest concept from Chroma).

**Acceptance:** running locally indexes ~the same chunk count as Chroma did; second run is a no-op.

### T04 — Re-implement `/search` and `/fetch` on pgvector **[M]**

- Replace the body of those handlers in `backend/app/main.py`.
- Same JSON shape — the released CLI **must not break**.
- Port any score normalization logic.
- Keep `/feedback` and `/telemetry` unchanged (not vector-backed).

**Acceptance:** `npx @elmundi/ship-cli search "auth"` returns the same kinds of results it did before. Snapshot test in `cli/tests/` (if missing, add one).

### T05 — Remove Chroma from runtime **[S]**

- Delete the lifespan branches in `main.py` that init Chroma.
- Delete `backend/.chroma/` from disk (and from any volume mounts in `docker-compose.yml`, `deploy/backend/Dockerfile`).
- `chromadb` removed from `requirements-backend.txt`.

**Acceptance:** `pip install -r requirements-backend.txt && python -c "import chromadb"` fails (pkg gone), backend boots without complaint.

### T06 — Backup / migration story **[S]**

- For self-hosted users: the upgrade simply re-indexes from source files on startup. No data loss because Chroma was a *cache* of static repo files, not the source of truth.
- For Bunny prod: same — first deploy with the new image will rebuild the index in pgvector.
- Note this in `documentation/CHANGELOG.md` under "Phase 10".

**Acceptance:** changelog entry exists; one ops dry-run on a staging container completes the rebuild successfully.

### T07 — Cleanup tests **[S]**

- `backend/tests/` — find any test that mocks Chroma; replace with the pgvector path.
- Drop `chromadb` from the test requirements if it was in there.

**Acceptance:** `pytest backend/tests -q` green without `chromadb`.

### T08 — Smoke against deployed CLI **[S]**

- After deploy: `npx @elmundi/ship-cli@latest search "policies"` against `https://ship.elmundi.com`.
- Compare result count and ordering with a saved baseline from before the cutover.

**Acceptance:** results within tolerance; CLI users see no degradation.

## Definition of done

- [ ] Zero `chromadb` imports in `backend/`.
- [ ] `requirements-backend.txt` does not pin `chromadb`.
- [ ] `.chroma/` directory removed and gitignored entry deleted.
- [ ] CLI search / fetch unchanged in observable behaviour.
- [ ] One Postgres image (already pgvector) is the only vector store.

## Risks / unknowns

- **Embedding cost** — re-indexing the methodology corpus on every cold start could be expensive. Mitigation: persist `content_sha`, only embed new/changed chunks. Manifest pattern keeps that.
- **Different score calibration** — pgvector's cosine distance may not be score-comparable to Chroma's. Test with a few known-good queries and tune ranking if needed (e.g. add a recency or kind weight).
- **HNSW index build time** — first `CREATE INDEX` on a populated table can be slow on a tiny container. Run during a deploy maintenance window or use `CREATE INDEX CONCURRENTLY`.

## Out of scope

- Switching embedding model (stays `text-embedding-3-small`).
- Adding hybrid (BM25 + vector) ranking — possibly a follow-up.
- Replacing OpenAI embedding with a local model — separate tradeoff.
- Migrating any other ChromaDB users (there are none in this repo).

## Audit findings (2026-04-30)

**Total references:** 10 (all in runtime code + 1 disk artifact + 1 build dependency).

### Runtime code — IndexStore lifecycle and vector search
- `backend/app/main.py:15` — `import chromadb` statement
- `backend/app/main.py:33` — `CHROMA_DIR = APP_ROOT / "backend" / ".chroma"` constant definition
- `backend/app/main.py:34` — `CHROMA_MANIFEST_PATH = CHROMA_DIR / "manifest.json"` constant definition
- `backend/app/main.py:119` — `self.client: chromadb.ClientAPI | None = None` type annotation in IndexStore.__init__
- `backend/app/main.py:126` — `chromadb.utils.embedding_functions.OpenAIEmbeddingFunction()` call in _embedding_function()
- `backend/app/main.py:187` — `CHROMA_MANIFEST_PATH.exists()` check in _needs_reindex()
- `backend/app/main.py:190` — `CHROMA_MANIFEST_PATH.read_text()` in _needs_reindex()
- `backend/app/main.py:196` — `CHROMA_DIR.mkdir()` in _write_manifest()
- `backend/app/main.py:197` — `CHROMA_MANIFEST_PATH.write_text()` in _write_manifest()
- `backend/app/main.py:200-201` — `CHROMA_DIR.mkdir()` and `chromadb.PersistentClient()` initialization in ensure_ready()

### Disk artifacts
- `backend/.chroma/` — persisted vector index directory (exists on disk)

### Build / deployment
- `requirements-backend.txt:69` — `chromadb>=0.5,<1` dependency pin

### Tests
- No chromadb references found in `backend/tests/` (not directly mocked)

### Documentation
- `documentation/internal/closed-beta/E13-rip-chroma.md` — task specification (this file; will be superseded)

### Removal checklist

1. **Delete runtime imports and constants** — remove lines 15, 33–34 from `backend/app/main.py`
2. **Remove IndexStore class** — delete entire `IndexStore` class from `backend/app/main.py` (lines ~117–260)
3. **Replace `/search` endpoint** — rewrite handler to query pgvector instead of Chroma collection
4. **Replace `/fetch` endpoint** — rewrite handler to use pgvector (or static file I/O for repo files)
5. **Remove manifest management** — delete `_needs_reindex()`, `_write_manifest()` methods; port logic to pgvector-based indexer
6. **Create methodology_chunks table** — add Alembic migration for pgvector-backed chunks table
7. **Create indexing service** — implement `backend/app/services/methodology_index.py` to populate chunks table
8. **Wire up startup indexing** — call indexing service in app lifespan (similar to current Chroma init)
9. **Remove chromadb dependency** — delete line 69 from `requirements-backend.txt`
10. **Delete persisted index** — remove `backend/.chroma/` directory entirely
11. **Update .gitignore** — remove `.chroma/` entry if present
12. **Verify Docker config** — confirm `Dockerfile` and `docker-compose.yml` do not reference `.chroma` volume (audit found none, but verify after removals)
13. **Run backend tests** — `pytest backend/tests -q` must pass without chromadb installed
14. **Smoke test CLI** — `npx @elmundi/ship-cli search "policies"` must return results via pgvector endpoint
