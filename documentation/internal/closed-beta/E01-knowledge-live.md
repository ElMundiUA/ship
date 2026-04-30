# E01 — Knowledge bucket UI live, no mock

**Priority:** P0
**Effort:** M (~5–7 days)
**Owner:** TBD

## Goal

The Knowledge surface in the console reads from the live `/v1` backend and stops rendering mock buckets/articles/sources. PO should be able to create a bucket, import a source, run the distiller, and see articles — all backed by Postgres + ChromaDB.

## Why

Knowledge is the product's central PO-facing argument: agents stop guessing because there is a curated, auditable context layer. The blog post **"The Inbox is not a backlog"** and **"Policies before prompts"** both lean on knowledge being a real surface. Today the UI shows fake buckets and the user can't tell which is real.

Backend already supports it: `routes/knowledge.py`, `routes/knowledge_import_sources.py`, `routes/distiller.py`, `routes/buckets_resolver.py`, `services/knowledge_ingestion.py`, `services/distiller.py`, `services/knowledge_dedup.py`, models in `db/models/agent_memory.py`.

Frontend gap: `console/src/app/knowledge/page.tsx` and `[id]/page.tsx` import `mockBuckets`, `mockDocs` from `lib/mock/cloud.ts` and never call the API.

## Tasks

### T01 — Audit current Knowledge backend surface **[S]**

- Read all routes under `/v1/workspaces/{ws}/buckets/*` and `/v1/workspaces/{ws}/knowledge/*`.
- Document the contract: list, get, create, patch, delete bucket; list articles in a bucket; create/edit article; CRUD on import sources; trigger distiller run.
- Append to [`backend/docs/knowledge-consolidation.md`](../../../backend/docs/knowledge-consolidation.md) if anything is undocumented.

**Acceptance:** a one-page route table covering everything the UI will need.

### T02 — Replace bucket list with live data **[M]**

- File: `console/src/app/knowledge/page.tsx`.
- Remove `mockBuckets`/`mockDocs` imports.
- Server component: `await getBuckets(workspaceId, token)`. Use existing `client.ts` helpers; add ones missing.
- Empty state: "No knowledge buckets yet. Create one to start curating context."
- Error state: `ApiUnavailableError` → "Knowledge service is unavailable. Retry."

**Acceptance:** a fresh workspace shows the empty state; an existing workspace with buckets shows real names + counts.

### T03 — Bucket detail page wired live **[M]**

- File: `console/src/app/knowledge/[id]/page.tsx`.
- Fetch one bucket + its articles + its import sources + distiller run history.
- Three tabs: **Articles**, **Sources**, **Distiller runs**. Each pulls from a separate endpoint; degrade independently.
- "Add article" / "Edit article" via `actions.ts` server actions calling POST/PATCH on `/v1/.../buckets/{slug}/articles`.

**Acceptance:** can navigate from list → detail → article and see real content; edit lands in DB; reloading shows persisted change.

### T04 — Import wizard live **[M]**

- File: `console/src/app/knowledge/import-wizard.tsx`.
- Already partly server-action wired — confirm it's calling the live `/v1/workspaces/{ws}/knowledge-import-sources` endpoint, not a stub.
- Add support for the source kinds the backend accepts: `static`, `repo`, `notion`, `linear` (whichever exist in `services/knowledge_ingestion.py`).
- Show last sync result + next sync window per source.

**Acceptance:** PO can configure a source, hit "Sync now", see article count change.

### T05 — Distiller runs surface **[S]**

- File: new component or extension of `[id]/page.tsx`.
- List recent distiller runs for the bucket: started_at, finished_at, status, articles in/out, deduper notes.
- "Run distiller" button → POST `/v1/workspaces/{ws}/buckets/{slug}/distill`.
- Live stream / poll for status while running.

**Acceptance:** clicking the button enqueues a run, the table reflects it, and finishing returns to a clean state.

### T06 — Connector + upload cards live **[S]**

- Files: `[id]/connector-card.tsx`, `[id]/upload-card.tsx`.
- Audit the existing UI; replace any local mock with real fetches.
- Upload: streams file → `POST /v1/.../articles/upload` (or whatever the backend exposes; if missing, log a P1 task to add it).

**Acceptance:** uploaded file shows up as an article with original filename and source provenance.

### T07 — Knowledge in onboarding wizard **[S]**

- The 5-step wizard's `confirm` step previews "what will land" — verify it includes default knowledge bucket seeding.
- File: `console/src/app/onboarding/*` and `services/seed_bundle.py`.
- If the seed bundle does not yet create a `product-knowledge` bucket, add it.

**Acceptance:** brand-new workspace lands with one default bucket and one starter article ("How this workspace was set up").

### T08 — Remove mock data file usage from Knowledge **[S]**

- After T02–T06 done, run a final sweep: `rg "mockBuckets|mockDocs" console/src/app/knowledge/`.
- All references gone or moved to a Storybook fixture file.

**Acceptance:** zero mock imports under `app/knowledge/`.

## Definition of done

- [ ] Knowledge list page renders live bucket data, with empty + error states.
- [ ] Bucket detail page shows articles, sources, distiller runs from live API.
- [ ] Import wizard creates real `KnowledgeImportSource` rows.
- [ ] Distiller can be triggered and completes, with the article count moving.
- [ ] No `mockBuckets`/`mockDocs` references in `console/src/app/knowledge/`.
- [ ] `make dev-local` smoke: create bucket → upload doc → run distiller → see article.

## Risks / unknowns

- ChromaDB embedding pipeline may be slow on first request; UX should not block.
- Notion / Linear connectors may not be production-ready; gate them behind a feature flag and ship `static` + `repo` first.
- Article body editor: rich Markdown vs plain text — pick plain Markdown for beta.

## Out of scope

- Knowledge graph visualization (later epic).
- Per-article RBAC.
- Cross-workspace knowledge sharing.
- Auto-suggest knowledge gaps from inbox patterns.
