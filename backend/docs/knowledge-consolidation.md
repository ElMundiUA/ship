# Knowledge buckets consolidation — plan

**Status:** Phases 1–3, 4a, 4b, 5a, 5b, 5c, 5d, 6a, 6b, 6c, 7a,
7b, 7c, 8 landed. Backend consolidation closed — `bucket_articles` is
the sole read surface (retriever, agent tools, `/articles`
endpoint, `summary_count` on bucket listings); `bucket_summaries`
is maintained for write-back compat only (deprecated, removal in
Phase 9). Phase 4a + 4b shipped the scope pill + scope-aware
`/knowledge`, `/catalog`, `/clarifications`, `/improvements`,
`/chat`. Phase 6a shipped the Distiller stub; Phase 6b added the
LLM-backed classifier (`classifier=auto|stub|llm`). Phase 6c
shipped the inbound adapters. Phase 7a added the console upload
surface. Phase 7b wired the connector-bucket create+sync surface
with a stub body. Phase 7c slots in real connector fetchers:
`backend/app/services/connectors/` now hosts a registry
(dispatched by `Integration.kind`) plus two concrete fetchers —
**Notion** (`resource_ref={page_id}`, renders page blocks to
markdown: headings, lists, to-do, quote, callout, code, divider,
inline formatting) and **Linear** (`resource_ref={issue_id}`,
renders a Linear issue into an H1 header + summary callout +
verbatim description + priority/team/labels/updated metadata
block). Both use GraphQL/REST via injectable `httpx.AsyncClient`
for deterministic tests. Unsupported `resource_ref` shapes fall
back to the stub with a logged warning, so Phase 7b buckets keep
working. Phase 8 slots in the per-user memory bucket: a shared
visibility helper (`scope=user` rows only visible to owner) is now
enforced across `retrieve_buckets`, `search_buckets` tool,
`list_buckets`, and `create_bucket`; `ensure_user_memory_bucket`
helper lazily mints `my-memory` (slug stable) on first use; new
`POST /chat/threads/{id}/save-to-memory` packs a thread into the
caller's private bucket without archiving. Next: multi-page
sync (Notion database, Linear team), Confluence fetcher (same
registry), consent toggle for auto-save, Phase 9 (scope-driven
sidebar IA).
**Scope:** unify the three "knowledge" surfaces (agent-memory buckets,
`.ship/knowledge/*.md` disk-lister, `KbChunk` RAG index) under one
`Scope × Source × Article` model, so every knowledge bucket has an
explicit visibility layer and an explicit content provenance.

This doc is the source of truth for the phasing. Keep it up to date
when a phase lands — it's what the next colleague opens first.

---

## Background

See the repo's existing notes for context:

- `backend/docs/knowledge-kind-task-source.md` — separate concern
  (the `knowledge` *artifact kind* in the catalog, not the
  `knowledge_buckets` table).
- `backend/migrations/versions/0010_agent_v2.py` — original agent
  surface (`knowledge_buckets`, `bucket_summaries`, `kb_chunks`).
- `backend/app/services/knowledge_lister.py` — disk scanner for
  `.ship/knowledge/*.md` (behind the `/v1/workspaces/{ws}/knowledge`
  endpoint; works for local `ArtifactRepo` rows only).
- `backend/app/services/agent/kb_indexer.py` — GitHub-aware indexer
  that chunks + embeds `.ship/knowledge/*.md` into `kb_chunks`, fed
  by the push webhook.

Three disjoint surfaces today:

1. **`knowledge_buckets` / `bucket_summaries`** — packed chat memory,
   workspace-scoped. Retrieval via pgvector over summaries. Owned by
   Navigator.
2. **`knowledge_lister`** — disk-scan of `.ship/knowledge/*.md` from
   `ArtifactRepo`. Separate dataclass named `KnowledgeBucket` (name
   collision with #1). Only works when the repo lives on Ship's disk
   — **essentially empty in SaaS today**. Returned by the console's
   `/knowledge` page.
3. **`kb_chunks`** — embedded + chunked `.ship/knowledge/*.md` from
   activated `WorkspaceRepo` rows via the push webhook / manual
   reindex. Read by `search_repo_kb` tool. Never surfaced as a bucket
   list to users.

Consolidation target: one `knowledge_buckets` table that spans all
three, classified by `scope_kind × source_kind`. Articles (future
`bucket_articles` table) replace the current `BucketSummary` + disk
files + (optionally) kb_chunks.

---

## Data model (target)

### Scopes — `knowledge_buckets.scope_kind`

Inheritance (low → high priority; later wins):

```
workspace  →  project  →  repo
                 +
               user  (parallel private overlay)
```

`global` (platform-level) is deferred until the platform-admin
surfaces exist and `workspace_id` can be made nullable. Until then,
cross-workspace sharing is a resolver concern, not a storage one.

### Sources — `knowledge_buckets.source_kind`

| source_kind         | auth origin                                         | where content lives                             |
| ------------------- | --------------------------------------------------- | ----------------------------------------------- |
| `agent_memory`      | packed chat summaries (Navigator)                   | `bucket_summaries` today; articles in Phase 5   |
| `repo_files`        | `.ship/knowledge/*.md` in an activated repo         | git is canonical; indexed into articles/kb      |
| `external_static`   | files/URLs uploaded into Ship                       | Ship object store                               |
| `connector_proxy`   | live third-party source (Confluence / ServiceNow)   | fetched on read via existing `Integration` row  |
| `audio_transcript`  | recorded interview (e.g. offboarding)               | object store + distilled articles               |

### Carriers (FKs)

`knowledge_buckets.(project_id, repo_id, user_id)` — exactly one is
non-null per scope_kind (enforced by `ck_knowledge_buckets_scope_carrier`):

| scope_kind  | carrier FK   |
| ----------- | ------------ |
| `workspace` | none         |
| `project`   | `project_id` |
| `repo`      | `repo_id`    |
| `user`      | `user_id`    |

### Source pointer

`knowledge_buckets.source_ref` JSONB — shape depends on `source_kind`:

```jsonc
// source_kind = repo_files
{ "path": ".ship/knowledge/code-style.md", "content_sha": "...", "branch": "main" }

// source_kind = connector_proxy
{ "integration_id": "<uuid>", "space_key": "ENG", "page_id": "..." }

// source_kind = external_static
{ "object_key": "s3://…", "mime": "text/markdown" }

// source_kind = audio_transcript
{ "recording_id": "<uuid>", "distilled_article_ids": ["..."] }
```

---

## Phasing

Each phase is **aditive + reversible** (Alembic up/down), and lands
on `main` behind its own tests. No phase is allowed to break an
existing flow — consolidation happens behind the public contract.

### Phase 1 — Foundation (✅ shipped `a7c8edd`)

Add `scope_kind / source_kind / source_ref / project_id / repo_id /
user_id` to `knowledge_buckets`. CHECK enforces carrier alignment.
Four partial-unique indexes (per scope_kind) replace the old
workspace-wide unique. Public CRUD still only creates workspace-scoped
agent-memory rows — output echoes new fields for consumers.

**Ships:** migration `0014_bucket_scope_source`, `BucketScope` +
`BucketSource` enums, `_serialize_bucket` helper, 6 new pytest
assertions (CHECK + partial uniques + API echo defaults).

### Phase 2 — Unify `.ship/knowledge/*.md` into DB buckets

Sync `.ship/knowledge/*.md` files in every activated `WorkspaceRepo`
into `knowledge_buckets` rows where `scope_kind='repo'` and
`source_kind='repo_files'`. Triggers:

- Push webhook (alongside the existing `KbChunk` reindex path).
- Manual reindex endpoint.
- First-time repo activation.

`/v1/workspaces/{ws}/knowledge` reads from DB rows (not the disk
lister) so the list page is finally populated for SaaS workspaces.
The old `knowledge_lister` stays as a fallback for local
`ArtifactRepo` rows — single entry point; DB wins when present.

**Acceptance:**

- One bucket row per `.ship/knowledge/*.md` file in an activated repo.
- Idempotent (re-sync by `source_ref.content_sha`).
- Deleting the file archives the bucket (not deletes — history
  preserved).
- `GET /v1/workspaces/{ws}/knowledge` returns rows with
  `scope_kind='repo'`, `source_kind='repo_files'`, `source_ref`.

### Phase 3 — Resolver

Endpoint `GET /v1/workspaces/{ws}/buckets/resolved?repo=…&project=…&user=me`
returns ordered list with `effective_scope`, applying the inheritance:

```
workspace ≺ project ≺ repo
      ⊕
       user   (per-user overlay — private; visible only to caller)
```

Output sorted low → high priority; consumers can either take all
(for "what's in scope") or dedupe by slug and keep highest-priority
(for "effective bucket").

### Phase 4 — Scope pill (frontend, first visible slice) — **landed**

AppShell top bar gets a universal `scope-pill`. **Phase 4a (shipped)**
surfaces three levels — `Workspace / <Repo> / Mine` — driven by
URL query params (`?scope=workspace|repo|user&repo_id=...`). No
cookie yet; URL is the single source of truth so Server Components
can mirror the state without hydration dance and bookmarks stay
valid. `<Project>` appears once the backend exposes a projects API
(Phase 9).

First consumer: `/knowledge` — when scope is anything other than
workspace, the page calls `GET /v1/workspaces/{ws}/buckets/resolved`
(Phase 3) and renders only the rows marked `effective=true`. The
workspace default still reads from the legacy
`/v1/workspaces/{ws}/knowledge` endpoint so we don't regress the
markdown-card grid users see today.

**Phase 4b (shipped)** propagated the pill to `Catalog`,
`Clarifications`, `Improvements` and `Navigator /chat`. Because the
underlying list endpoints don't yet accept a `repo_id` filter, the
pages filter client-side:

- **Catalog** — repo scope keeps rows whose `source_repo_id` matches
  the selected repo *plus* all global/workspace-authored rows the
  repo inherits; user scope shows an advisory banner and falls back
  to full.
- **Clarifications / Improvements** — repo scope filters by
  `row.repo_id`; user scope surfaces a banner (no "assigned to me"
  concept yet) and keeps the full list. Tab links preserve the pill
  state so flipping `status=answered` doesn't drop the scope.
- **Navigator** — pill narrows the memory-bucket sidebar via a
  client-side `BucketScopeFilter`. Active-thread selection still
  ignores `repo_id` (real repo-scoped threads need backend work);
  the page shows a banner when scope ≠ workspace so users aren't
  surprised that the conversation surface is unchanged.

Follow-up consumers (tracked separately): Pipelines, Metrics,
Improvements analytics — and real backend filters once we push
`repo_id` into `GET clarifications / improvements / artifacts`.

### Phase 5 — Article table

Split into three landing slices so we can ship incrementally:

- **Phase 5a (landed).** New `bucket_articles` table
  (`id, bucket_id, slug, title, body_md, content_sha, version,
  supersedes_id, status, provenance jsonb, embedding,
  archived_at`). `sync_repo_files` dual-writes one article per
  `repo_files` bucket (slug=`"main"`, version bumps on SHA change,
  old row flips to `superseded` before the new `published` lands).
  Migration is add-only; no reads are cut over yet. Backfill for
  pre-Phase-5a tenants happens lazily on the next sync (the fast
  path still populates a missing article).
- **Phase 5b (landed).** Data migration: every `bucket_summaries`
  row gets a mirror `bucket_articles` row with slug
  `thread-<uuid-hex>`, the summary's embedding carried over verbatim,
  and provenance `{source_kind: "agent_memory", summary_id,
  thread_id, created_by_user_id, packed_at}`. `TopicService.pack_topic`
  dual-writes on new packs so the mirror stays current. Idempotent by
  deterministic slug; the bulk Python backfill and the SQL data
  migration can run in either order.
- **Phase 5c (landed).** Read-path cutover: `TopicService.retrieve_buckets`
  ranks and pulls from `bucket_articles` (published + unarchived +
  embedding-present + scope=agent_memory). `BucketHit.summary_id`
  renamed to `article_id`; `BucketHit.summary` now carries
  `article.body_md`. Write-back to `bucket_summaries` is preserved by
  the pack_topic dual-write so the still-legacy reads stay correct.
- **Phase 5d (landed).** Finishes the backend read-path cutover:
  - Agent tools `search_buckets`, `get_knowledge_bucket`,
    `list_buckets` now rank/project over `bucket_articles`. Same
    JSON keys on the wire (`summaries` / `summary_count` are
    preserved) but `articles` / `article_count` are exposed as the
    new canonical names so the Phase 4 UI + a future tool-spec
    refresh can pick them up.
  - New endpoint `GET /v1/workspaces/{ws}/buckets/{slug}/articles`
    returns articles with `{id, slug, title, body_md, version,
    status, provenance, created_at, updated_at, archived_at}`.
    Optional `include_superseded` / `include_archived` flags expose
    Phase 5a's version history for an admin timeline view; the
    default view mirrors the retriever's filter.
  - `BucketOut.summary_count` in the `/v1/workspaces/{ws}/buckets`
    list / get / patch now counts published articles (1:1 with
    summaries for agent_memory thanks to Phase 5b; a real,
    non-zero count for repo_files buckets for the first time).
  - Legacy `GET /v1/workspaces/{ws}/buckets/{slug}/summaries`
    remains unchanged (reads `bucket_summaries` directly) and is
    marked deprecated; removal target is Phase 9 after the frontend
    migration to `/articles` lands.

### Phase 6 — Distiller contract

`POST /v1/workspaces/{ws}/buckets/{slug}/distill` with an input blob
(PR diff, upload, webhook payload, transcript chunk) →
`{ run, decision: "new" | "update" | "skip", article_ids, reason }`.

**Phase 6a (shipped).** Synchronous stub classifier backed by a new
`distiller_runs` table (migration `0017_distiller_runs.py`). Every
ingest writes a run row (audit trail) and decides deterministically:

- existing published article under the same slug with the same
  `content_sha` → `skip`;
- existing published article with a different body → `update`
  (old row flipped to `superseded`, version bumped, new row
  inserted as `published`);
- empty body → `skip`;
- otherwise → `new` (insert version 1 with a derived slug +
  first-line title + best-effort embedding).

A companion `GET /buckets/{slug}/distill/runs` endpoint surfaces
history newest-first so the upcoming Knowledge panel can render
"what the Distiller did against this bucket" without joining on
`bucket_articles` directly. RBAC: `ROLES_MAINTAIN` to write,
`ROLES_READ` to read.

Console client helpers (`distillBucket`, `listDistillerRuns`) live
in `console/src/lib/api/client.ts`. No UI yet — Phase 7 adds the
ingest surfaces that drive it.

**Phase 6b (shipped).** LLM-backed classifier. `run_distiller`
gained a `classifier: Classifier | None = None` parameter; the
stub is now `classify_stub` in `backend/app/services/distiller.py`
and the LLM impl lives in `backend/app/services/distiller_llm.py`.
The LLM variant pulls up to 20 published articles from the target
bucket (newest first), renders a single-turn prompt that asks for
a strict JSON verdict (`decision`, `slug`, `title`, `target_slug`,
`reason`, `reasoning`), and calls `AgentClient.acomplete` with
`response_format={"type": "json_object"}` + temperature 0.1. A
reconciliation pass (`_reconcile_classification`) then validates
the verdict against DB reality — mapping `update`-with-unknown-
target to `new`, demoting `new`-over-live-slug to `update`, and
forcing `skip` on empty bodies — so the LLM can never cause an
incorrect supersede.

The HTTP layer (`backend/app/api/v1/routes/distiller.py`) exposes
a `classifier` field on `DistillIn` (`auto` | `stub` | `llm`).
`auto` picks the LLM when `pick_default_client()` resolves and
silently falls back to the stub when it doesn't; `stub` pins the
deterministic classifier for replays; `llm` requires an agent
client and returns 503 otherwise. Any classifier exception inside
`run_distiller` is caught and demoted to the stub — ingest never
hard-fails because the model is flaky.

Audit trail: the run row's `output_refs.classifier` now carries
`{ name: "stub" | "llm", reasoning, vendor? }` so operators can
trace every decision.

**Phase 6c (shipped).** Inbound adapters live in
`backend/app/services/distiller_sources.py` — three thin
`Classifier`-agnostic functions that every transport (webhook,
HTTP upload, connector job) routes through, plus an
`ensure_bucket` helper that satisfies the `(workspace_id,
scope_kind, carrier_id, slug)` uniqueness invariant before
calling `run_distiller`.

- `ingest_pr_merge(session, workspace_id, repo, payload)` —
  builds a markdown body from the PR (title, author, merged-by,
  merged-at, branch, description), deterministic slug
  `pr-<number>`, provenance `{kind:"pr_merged", pr_number,
  html_url, author, merged_at, head_ref, base_ref}`. Idempotent
  on replay (content_sha dedupe). Skips unmerged payloads and
  `ship/install-*` PRs. Called from `_apply_pull_request_event`
  inside a best-effort try/except so a flaky Distiller never
  poisons the webhook 200.

- `ingest_external_static_upload(session, bucket, filename,
  content_type, body_md)` — slug derived from filename (strip
  ext, slugify), provenance `{kind:"external_static_upload",
  filename, content_type, uploaded_at}`. Wired to
  `POST /v1/workspaces/{ws}/buckets/{slug}/upload` (multipart,
  `file` + optional `classifier`), 1 MiB cap, UTF-8 strict,
  allowed types `text/plain|text/markdown` or `.md/.markdown/.txt`.

- `ingest_connector_page(session, bucket, connector_kind,
  page_ref, body_md)` — stub shape for the future
  Notion/Confluence/ServiceNow adapter. Records connector name +
  page ref on provenance; actual fetchers will live under
  `backend/app/services/connectors/*`.

Tests: `backend/tests/test_distiller_sources.py` (8) covers
`ensure_bucket` happy + two error paths, PR-merge happy +
idempotent replay + unmerged/install skips, upload filename →
slug; `backend/tests/test_distiller_pr_webhook.py` (2) locks
the webhook → adapter wire and the unmerged skip;
`backend/tests/test_v1_distiller_upload.py` (6) covers the HTTP
upload route end-to-end (new, replay-skip, oversize 400, wrong
content-type 400, non-UTF-8 400, missing-bucket 404). Console
client now exposes `uploadToBucket` for the server-side surface
Phase 7 will mount.

Added dependency: `python-multipart` (FastAPI's multipart form
parser) in `requirements-backend.txt`.

### Phase 7 — New sources

Per-source surface:

- **external-static** — upload flow (file / URL paste) in the
  Knowledge page. Object storage behind a signed URL.
- **connector-proxy** — read-only bucket backed by an existing
  `Integration` row. Confluence space / Notion database / ServiceNow
  KB. Lazy fetch on read; cached in articles only when the Distiller
  actually ingests them.
- **audio-transcript** — upload audio → ASR → transcript → Distiller.
  Offboarding flow: agent runs the interview over Navigator, records
  answers, distills into the leaving person's repo/project buckets
  with a review gate before publishing.

#### Phase 7a — Upload picker in `/knowledge` (shipped)

- `console/src/app/knowledge/[id]/page.tsx` — rewired to fan out the
  legacy `getKnowledgeBucket` fetch alongside the unified
  `getBucket`, `listBucketArticles`, and `listDistillerRuns` calls.
  Each sub-fetch tolerates 404/errors independently so repo-files
  buckets without a unified row (and external-static buckets without
  a repo mirror) both render cleanly; the page only hard-404s when
  all the bucket lookups miss.
- `console/src/app/knowledge/[id]/actions.ts` — server action
  `uploadBucketFileAction(workspaceId, slug, formData)` decodes the
  FormData (`file`, `classifier`), validates size (≤1 MB) and
  extension/mime (`.md|.markdown|.txt`) client-side before routing
  through `uploadToBucket`. After a successful upload it
  `revalidatePath`s the bucket page so the articles + runs cards
  reflect the new row without a client-side refresh. Returns a
  discriminated `UploadActionResult` instead of throwing so the
  client can surface the decision / error inline.
- `console/src/app/knowledge/[id]/upload-card.tsx` — client component
  with a drag-style file input, classifier selector
  (`auto | stub | llm`), submit button wired through `useTransition`,
  and an inline result banner showing the Distiller `decision`,
  picked `classifier`, and message. Hidden by the server page unless
  the bucket's `source_kind` is `external_static` or
  `audio_transcript` so `repo_files` surfaces stay read-only and
  `agent_memory` buckets aren't tempted into manual edits.
- Page additions beyond the upload card:
  - **Articles card** renders the `bucket_articles` rows with
    title, slug, version, status badge, and a provenance hint
    (extracts `pr_number`/`author` for PR-merge articles, filename
    for `external_static_upload`, path for repo files, thread id
    for agent memory).
  - **Runs card** lists the last 20 `DistillerRun` rows with
    decision badge (`new`/`update`/`skip`/`error`), picked
    classifier name from `output_refs.classifier.name`, and
    relative timestamp.
- `e2e/tests/knowledge-upload.wired.spec.ts` — Playwright smoke
  that seeds a fresh external-static bucket via the Ship API, drops
  a markdown file through the upload card (pinned to
  `classifier=stub` for determinism), and asserts the success
  banner + articles table update. Also covers the oversize rejection
  path client-side.

#### Phase 7b — Connector-proxy create + sync (shipped)

- `backend/app/api/v1/routes/chat.py` — `BucketCreateIn` now carries
  the consolidation surface (`scope_kind`, `source_kind`,
  `source_ref`, carrier FKs). `create_bucket` validates them against
  the same rules as `ensure_bucket`, and for `connector_proxy`
  additionally verifies the `source_ref.integration_id` is a UUID
  pointing at an `Integration` row in the same workspace. The
  stored `source_ref` is normalized (adds `integration_kind`
  echoed from the Integration, preserves whatever `resource_ref`
  the caller sent). Workspace-level uniqueness stays intact; repo/
  project/user scopes rely on the partial unique indexes from
  `0014_bucket_scope_source`.
- `backend/app/api/v1/routes/distiller.py` — `POST /workspaces/{ws}/
  buckets/{slug}/sync` drives `ingest_connector_page` with a
  deterministic stub body (pinned to `classifier=stub`). The stub
  synthesizes a compact markdown page from `integration_kind` +
  `resource_ref` so the Distiller's new/update/skip transitions are
  observable in the UI even before the real fetcher layer exists.
  The surface contract stays stable once Phase 7c wires actual
  Confluence/Notion/Linear readers — only the fetched body changes.
- `backend/tests/test_v1_connector_bucket.py` — new test module
  covering: create persists normalized `source_ref`, rejects missing
  `integration_id`, rejects invalid UUID, rejects cross-tenant
  integration ids; sync creates + records a `DistillerRun`, is
  idempotent (second sync → `skip`), rejects non-connector buckets,
  404s on unknown slugs.
- `console/src/lib/api/client.ts` — `CreateBucketInput` gains the
  consolidation fields; new `createConnectorBucket` helper shapes
  the `source_ref` and delegates to `createBucket`. New
  `syncConnectorBucket` helper wraps the `/sync` route.
- `console/src/app/knowledge/actions.ts` — `createBucketAction`
  server action that handles both bucket kinds, redirects to
  `/knowledge/<slug>` on success, returns an inline error otherwise.
- `console/src/app/knowledge/new-bucket-dialog.tsx` — inline client
  panel with Upload / Connector tabs. Loads integrations server-
  side and renders them in the picker; the Connector tab is
  disabled when no integrations are configured. Free-form
  `resource_ref` is a JSON textarea so it's source-agnostic —
  Notion uses `{database_id}`, Confluence `{space_key}`, etc.
- `console/src/app/knowledge/[id]/actions.ts` — adds
  `syncConnectorBucketAction` which wraps `syncConnectorBucket` +
  `revalidatePath`.
- `console/src/app/knowledge/[id]/connector-card.tsx` — client
  component that renders the integration kind, `resource_ref`
  entries, and a Sync-now button wired through
  `useTransition`. Inline result banner shows the Distiller
  decision so the `new → skip` transition on re-sync is visible.
- `console/src/app/knowledge/[id]/page.tsx` — mounts the connector
  card when `source_kind === connector_proxy`, and extends
  `provenanceHint` to render "synced from <connector>" on the
  articles table.
- `e2e/tests/knowledge-connector.wired.spec.ts` — Playwright smoke
  that PUTs a throwaway webhook integration, creates a connector
  bucket via the API, opens `/knowledge` to confirm the dialog
  renders both tabs, navigates to the detail page, asserts the
  connector card mounts with the right provider + resource_ref,
  clicks Sync now and asserts `new → skip` transitions.

#### Phase 7c — Real connector fetchers (shipped)

- `backend/app/services/connectors/__init__.py` — registry of
  fetchers keyed by `Integration.kind`. Exposes a `@register(kind)`
  decorator, a `fetch_connector_pages(integration, resource_ref)`
  dispatcher, and `set_http_client_override` for test injection
  (`MockTransport`-based clients). Three error shapes separate
  "expected fallback" from "operator must act":
  `ConnectorUnsupported` (fetcher can't handle the shape — caller
  falls back to stub with a warning), `ConnectorConfigError`
  (integration row is broken — caller returns 502), plain
  `ConnectorError` for anything else.
- `backend/app/services/connectors/notion.py` — first registered
  fetcher. Handles `resource_ref={"page_id": "<uuid>"}` by calling
  `GET /v1/pages/{id}` + `GET /v1/blocks/{id}/children` (with
  pagination and a `_MAX_BLOCKS=200` cap), then renders the blocks
  to markdown. Covered block types: heading_1/2/3, paragraph,
  bulleted_list_item, numbered_list_item, to_do, quote, callout
  (with emoji icon), code (fenced with language), divider,
  child_page (link). Rich-text renderer supports bold/italic/
  strikethrough/code/link. Secret decrypt runs *after* the shape
  check so an integration missing its token but targeting an
  unsupported shape still resolves to stub fallback instead of
  502'ing. Notion 401/403/404 are surfaced as
  `ConnectorConfigError` with a "is the integration shared with
  the page?" hint — the single most common operator error.
- `backend/app/api/v1/routes/distiller.py` — `/buckets/{slug}/sync`
  now resolves the `Integration` row and tries the registry. If a
  fetcher returns a page, its body drives `ingest_connector_page`.
  If it doesn't (no fetcher registered or shape rejected), the
  Phase 7b stub body path runs unchanged. `ConnectorConfigError`
  becomes 502 with the fetcher's detail message; a missing
  integration row logs + falls back to stub (the bucket row is
  still referentially valid). Response shape
  (`DistillOut` — single run, single `article_ids` entry) is
  unchanged, so the console's `syncConnectorBucketAction` keeps
  working. Multi-page sync is deferred: if a fetcher ever returns
  more than one page we ingest the first and log the rest for
  follow-on Phase 7d.
- `backend/tests/test_connectors_notion.py` — 7 unit tests
  against `httpx.MockTransport`: kitchen-sink block rendering,
  `{database_id}` and missing `page_id` return empty (fallback),
  shape-check-before-secret invariant, missing secret raises
  ConfigError, Notion 404 produces share hint, pagination follows
  `next_cursor`.
- `backend/tests/test_v1_connector_sync_notion.py` — 4 endpoint
  integration tests using `set_http_client_override`. Real
  fetcher path ingests real markdown (not the stub banner),
  re-sync is `skip`, missing-secret is 502, Notion 404 is 502.
  Together with the Phase 7b tests that use `notion` + unsupported
  shape, the fallback invariant is nailed on both sides.
- `backend/app/services/connectors/linear.py` — second registered
  fetcher. Handles `resource_ref={"issue_id": "<id or ELM-42>"}`
  by POSTing one GraphQL query to `api.linear.app/graphql` and
  rendering the returned issue as:
  `# <identifier> · <title>` header, one summary callout line
  (`> [Open in Linear](…) · state: … · assignee: …`), the raw
  Linear `description` (already markdown), and a footer meta
  block (priority label, team key+name, sorted labels,
  `updatedAt`). Deterministic enough that re-sync with unchanged
  upstream collapses to `skip`. Error mapping: 401/403 → immediate
  `ConnectorConfigError` (reconnect); GraphQL `FORBIDDEN` /
  `AUTHENTICATION_ERROR` in the error envelope → `ConnectorConfigError`
  with "is the integration shared with the issue's team?" hint;
  `data.issue==null` (id doesn't exist, or isn't visible to the
  token) → `ConnectorConfigError` with a "not visible" hint;
  other GraphQL errors → `RuntimeError` for 502. No `page_ref`
  normalisation — we pass through whatever identifier the operator
  gave us (Linear's API accepts both UUID and `ENG-123`).
- `backend/tests/test_connectors_linear.py` — 9 unit tests
  (MockTransport-backed): ELM-style happy path renders full
  markdown, `{team_key}` and missing `issue_id` fall back,
  shape-check-before-secret invariant, missing secret → ConfigError,
  401 → ConfigError, GraphQL FORBIDDEN → share hint,
  `data.issue=null` → not-visible hint, empty description renders
  a stable `_(no description)_` placeholder so `content_sha` is
  deterministic.
- `backend/tests/test_v1_connector_sync_linear.py` — 4 endpoint
  integration tests mirroring the Notion suite. Real Linear path
  ingests real markdown, re-sync → `skip`, missing-secret → 502,
  401 → 502. Combined Phase 7c regression: 32 connector tests
  pass, no flakes on repeat runs.

### Phase 8 — User-memory bucket

Per-user bucket (`scope=user`, `source_kind=agent_memory`) minted
lazily on first save. Retrieval treats it as a private overlay
alongside workspace/project/repo buckets (Phase 3 ladder already
gave it the highest priority for the caller). Navigator writes via
an explicit user action (`save-to-memory`) rather than an
automatic end-of-thread summary — when a consent toggle lands it
will reuse the same endpoint so the write path stays stable.

What landed:

- `backend/app/services/bucket_visibility.py` — one-liner
  predicate `visible_to_user_clause(caller_user_id)`. Admits every
  non-USER scope; admits USER-scoped rows only when `user_id`
  matches the caller. Composes into any existing `select(KB)`
  without changing callers' workspace / archived / status filters.
- `backend/app/services/agent/topic.py` — `retrieve_buckets`
  composes the helper. Same Phase 5c / `agent_memory` / published
  / embedded filters, plus the visibility clause — another
  user's packed thread can no longer leak into the caller's
  warmed-context prompt.
- `backend/app/services/agent/tools.py` — `search_buckets` tool
  mirrors the same clause so the LLM can't sidestep privacy by
  routing through a tool call.
- `backend/app/api/v1/routes/chat.py`:
  - `list_buckets` gains the visibility clause so the console
    /knowledge surface never lists another user's private rows.
  - `create_bucket` with `scope_kind=user` now rejects a
    `user_id` that isn't `auth.user.id` with 403 — silent
    "bucket attributed to a user who can't read it" rows were
    possible before.
- `backend/app/services/distiller_sources.py` —
  `ensure_user_memory_bucket(session, workspace_id, user_id)`.
  Thin wrapper over the existing `ensure_bucket` with pinned
  slug (`my-memory`), name (`My memory`), scope (user), and
  source (agent_memory). Idempotent via the partial unique
  `uq_knowledge_buckets_user_slug` so concurrent first-writes
  don't race to create two rows.
- `POST /v1/workspaces/{ws}/chat/threads/{id}/save-to-memory` —
  Phase 8's headline endpoint. Loads the thread, mints
  `my-memory` if missing, and calls `pack_topic(bucket_id=...)`.
  Key differences from `/pack`: membership role is `ROLES_READ`
  (packing into your own bucket is a user action, not an admin
  one), thread stays `active` after save, and the target bucket
  is always the caller's `scope=user` row — no `bucket_id` /
  `bucket_slug` / `bucket_name` inputs. Response shape is the
  same `BucketSummaryOut` the console already renders.
- `backend/tests/test_v1_user_memory_bucket.py` — 11 tests:
  helper idempotency + per-user uniqueness, visibility predicate
  hides other users' USER rows and keeps workspace rows for all,
  `create_bucket` self-vs-other, `list_buckets` isolation,
  `save-to-memory` happy path (mints bucket, does not archive),
  idempotent second call, empty thread → 400, unknown thread
  → 404. Full regression: **450 / 450 pass**, no new flakes.

Explicitly out of scope (moves to a follow-up): automatic
end-of-thread save (needs consent UX), cross-thread "forget this"
button, per-user quota on `my-memory` articles.

### Phase 9 — IA restructure + sidebar

Left nav grouped by scope:

```
REPO      · Dashboard · Pipelines · Clarifications · Improvements · Knowledge
PROJECT   · Overview · Metrics · Knowledge
WORKSPACE · Portfolio · Catalog · Knowledge · Integrations · Members · Audit
ADMIN     · Settings · Tokens · Billing
ME        · Navigator · My knowledge · Tasks
```

Sidebar visibility reacts to the scope pill; Clarifications +
Improvements also respect it.

---

## Invariants (don't regress)

- **CHECK**: `scope_kind` and carrier FKs always aligned
  (`ck_knowledge_buckets_scope_carrier`).
- **Partial uniques**: same slug legal across different scopes /
  carriers; per-carrier uniqueness preserved.
- **Additivity**: every migration is reversible. No column dropped
  without a deprecation migration first.
- **API echo**: every consolidation column is exposed on the public
  response so external consumers can branch on it without round-trip
  queries.
- **No cross-phase leakage**: Phase 2 does not need Phase 3's
  resolver to be correct; Phase 3 does not need Phase 5's articles.

---

## Changelog

- **2026-04-21** — Phase 8 shipped: per-user memory bucket.
  Shared visibility helper
  (`backend/app/services/bucket_visibility.py`) enforces "user
  sees workspace/project/repo freely, USER-scoped only when they
  own it" at four choke points: `TopicService.retrieve_buckets`,
  the `search_buckets` agent tool, the `GET /buckets` list
  endpoint, and the `POST /buckets` create guard (403 on minting
  for another user). New `ensure_user_memory_bucket` helper lazily
  creates the canonical `my-memory` bucket (`scope=user`,
  `source_kind=agent_memory`) on first save — idempotent via the
  existing partial unique. New endpoint `POST
  /v1/workspaces/{ws}/chat/threads/{id}/save-to-memory` packs a
  thread into the caller's bucket without archiving the thread
  (companion to `/pack`) — membership role `ROLES_READ`, explicit
  user action = implicit consent. 11 new tests
  (`test_v1_user_memory_bucket.py`): helper idempotency + per-user
  uniqueness, visibility predicate, `create_bucket` self-vs-other,
  `list_buckets` isolation, save-to-memory happy path / idempotent
  resave / empty-thread / unknown-thread. Full regression: 450/450
  pass (pre-existing failures in `test_manifest_and_catalog.py` +
  `test_v1_workspace_artifacts.py` remain, same as Phase 7c).
- **2026-04-21** — Phase 7c extended: Linear fetcher landed.
  New module `backend/app/services/connectors/linear.py` registers
  for `Integration.kind='linear'` and handles
  `resource_ref={"issue_id": "<id or ELM-42>"}`. Fires one GraphQL
  query against `api.linear.app/graphql`, renders the issue as a
  deterministic markdown page (header, summary callout, verbatim
  description, priority/team/labels/updated footer). Error
  mapping: 401/403, GraphQL FORBIDDEN, and `data.issue=null` all
  become `ConnectorConfigError` with actionable hints instead of
  silent 502s. Dispatcher eager-loads both `notion` and `linear`
  modules at first call. 13 new tests: 9 fetcher unit
  (`test_connectors_linear.py`, MockTransport-backed) + 4 endpoint
  integration (`test_v1_connector_sync_linear.py`, via
  `set_http_client_override`). Combined connector suite: 32 pass.
- **2026-04-21** — Phase 7c shipped: first real connector fetcher.
  New package `backend/app/services/connectors/` holds a
  fetcher registry (`@register(kind)` decorator, `fetch_connector_pages`
  dispatcher, `set_http_client_override` test hook) plus a Notion
  fetcher that converts a single Notion page
  (`resource_ref={"page_id": "..."}`) into markdown via
  `GET /v1/pages/{id}` + paginated `GET /v1/blocks/{id}/children`.
  Renderer covers heading_1/2/3, paragraph, bulleted/numbered list,
  to_do, quote, callout (with emoji), code (fenced with language),
  divider, child_page link, and rich-text bold/italic/strike/code/
  href. Shape check runs before secret decrypt so buckets with
  unsupported shapes (e.g. `{database_id}`) fall back to the Phase 7b
  stub with a warning — 502 is reserved for "real fetcher, real
  breakage" cases (missing token, Notion 401/403/404). `/sync`
  endpoint rewired: Integration row resolved → registry dispatched
  → real body ingested if available, stub fallback otherwise.
  Single-page semantics preserve the `DistillOut` response shape
  unchanged for the console. 11 new tests: 7 fetcher unit
  (`test_connectors_notion.py`, MockTransport-backed) + 4 endpoint
  integration (`test_v1_connector_sync_notion.py`, using the
  override hook). Full bucket/distiller regression: 72 pass.
- **2026-04-21** — Phase 1 shipped (`a7c8edd`). Doc created (`821ff8d`).
- **2026-04-21** — Phase 2 shipped: `.ship/knowledge/*.md` files mirror
  into `knowledge_buckets` as `scope='repo'` / `source='repo_files'`
  rows. Sync triggers: push webhook, manual `kb/reindex`, first-time
  activation. `GET /v1/workspaces/{ws}/knowledge` reads from DB with
  legacy disk-lister as fallback. 10 new tests (sync service + route).
- **2026-04-21** — Phase 3 shipped: `GET /v1/workspaces/{ws}/buckets/resolved`
  returns the full scope ladder (workspace ≺ project ≺ repo ⊕ user)
  with `priority` + `effective_scope` + `effective` flags inline and
  a `winners_by_slug` quick-dedupe map. Caller's user overlay always
  included; other users' user-scoped rows invisible. 10 new tests.
- **2026-04-21** — Phase 5a shipped: `bucket_articles` table (migration
  `0015_bucket_articles`) + `BucketArticle`/`BucketArticleStatus`
  models + dual-write from `sync_repo_files`. One `main` article per
  `repo_files` bucket; SHA-based fast-path on no-op; version bumps +
  supersession on edits; archival on file deletion; resurrection
  picks `MAX(version)+1` so the partial-unique on published rows
  never collides with dormant history. Legacy rows without an
  article get backfilled on the next sync. 6 new tests on top of the
  existing sync suite (12 total).
- **2026-04-21** — Phase 5b shipped: `bucket_summaries` → `bucket_articles`
  mirror. New service `backend.app.services.bucket_summary_articles`
  exposes `mirror_summary_to_article`, `backfill_missing_articles_for_bucket`,
  `backfill_missing_articles_for_workspace`; `TopicService.pack_topic`
  dual-writes so new packs land in both stores. Alembic `0016_backfill_summary_articles`
  does a one-shot SQL backfill (pgcrypto `digest(...,'sha256')` for
  content_sha, slug `thread-<uuid-hex>`, embedding copied by pgvector
  cast). Idempotent by deterministic slug. 9 new tests.
- **2026-04-21** — Fixed `.github/workflows/e2e-console.yml` — the step-level
  `if: ${{ secrets.* }}` is forbidden by GitHub, which silently broke the
  workflow on every push. Moved the empty-secret bail into bash inside the
  step. `actionlint` clean, workflow files parse again.
- **2026-04-21** — Phase 5c shipped: `TopicService.retrieve_buckets` now
  ranks from `bucket_articles` instead of `bucket_summaries`, filtered to
  `status='published' AND archived_at IS NULL AND embedding IS NOT NULL
  AND source_kind='agent_memory'`. `BucketHit.article_id` replaces
  `summary_id`; the downstream prompt-assembly layer (`assemble_messages`,
  `_format_bucket_memory`) is unchanged. 5 new tests guard source-scope,
  superseded/archived filtering, similarity threshold, and NULL-embedding
  tolerance. Backend suite: 365 passed.
- **2026-04-21** — Phase 5d shipped: finishes the backend cutover.
  Agent tools `search_buckets` / `get_knowledge_bucket` / `list_buckets`
  read from `bucket_articles`; new console endpoint `GET /v1/workspaces/
  {ws}/buckets/{slug}/articles` returns the canonical article shape with
  optional `include_superseded` / `include_archived`; `BucketOut.summary_count`
  switched to counting published articles (now meaningful for repo_files
  buckets too). Legacy `GET .../summaries` stays as-is and is marked
  deprecated. 14 new tests (7 agent-tool cutover + 7 endpoint). Backend
  suite: 379 passed.
- **2026-04-21** — Phase 6a shipped: Distiller stub.
  `distiller_runs` table (migration 0017) + service in
  `backend/app/services/distiller.py` + v1 routes under
  `backend/app/api/v1/routes/distiller.py`. Deterministic classifier
  (empty → skip, matching content_sha → skip, different body
  same slug → update with version bump + supersedes, else new).
  Best-effort embedding keeps the article searchable when
  `OPENAI_API_KEY` is set; silently skipped otherwise. Seven unit
  tests cover new / update / skip-empty / skip-same-hash / 404 /
  400 / history listing. Console typed helpers added
  (`distillBucket`, `listDistillerRuns`); no UI yet.
- **2026-04-21** — Phase 6b shipped: LLM classifier for the
  Distiller. Introduced a `Classifier` protocol in
  `backend/app/services/distiller.py`; renamed the stub to
  `classify_stub`; added `_reconcile_classification` to tighten
  any verdict against DB reality before the write path runs.
  New module `backend/app/services/distiller_llm.py` implements
  `classify_with_llm` (and a `make_llm_classifier` adapter)
  talking to `AgentClient.acomplete` in JSON mode, with robust
  parsing + salvage-on-prose. `POST /distill` now accepts a
  `classifier` field (`auto` / `stub` / `llm`, default `auto`);
  `auto` falls back silently to the stub when no agent client
  resolves, `llm` returns 503 if none is configured. Every run
  records `{name, reasoning, vendor}` under
  `output_refs.classifier`. Five new unit tests (update /
  skip-with-reason / malformed-JSON-fallback / llm-503 /
  auto-fallback) on top of the seven phase-6a tests, all
  stub-pinned to stay deterministic in CI. Console client types
  extended (`classifier` on `DistillInput`, same on
  `ApiDistillOut`); no UI change yet.
- **2026-04-21** — Phase 6c shipped: inbound adapters.
  New module `backend/app/services/distiller_sources.py`
  exposes `ensure_bucket` (scope-aware get-or-create) and three
  classifier-agnostic inbound shims: `ingest_pr_merge` (hooked
  from `_apply_pull_request_event` in `github_app.py`, writes to
  a repo-scoped `pr-summaries` bucket with provenance
  `{kind:"pr_merged", pr_number, html_url, author, merged_at,
  head_ref, base_ref}`, skips `ship/install-*` PRs, idempotent
  on webhook replay), `ingest_external_static_upload` (backs the
  new `POST /v1/workspaces/{ws}/buckets/{slug}/upload` multipart
  route — 1 MiB cap, UTF-8 strict, `text/plain|text/markdown`),
  and `ingest_connector_page` (stub for the next connector
  integration). Console client gains `uploadToBucket`. Tests:
  8 unit (`test_distiller_sources.py`) + 2 webhook integration
  (`test_distiller_pr_webhook.py`) + 6 HTTP upload route
  (`test_v1_distiller_upload.py`) = 16 new tests, all green.
  A4 PR-merged-notification suite (6) still passes — the new
  Distiller hook is additive and best-effort. Dep added:
  `python-multipart` in `requirements-backend.txt`.
- **2026-04-21** — Phase 4b shipped: scope pill propagated to
  `/catalog`, `/clarifications`, `/improvements`, and `/chat`.
  Client-side filters on `source_repo_id` (catalog) and
  `row.repo_id` (clarifications / improvements); Navigator's
  buckets sidebar gains a `BucketScopeFilter` prop that filters
  memory buckets by repo/user scope while keeping ambient
  workspace rows visible (inheritance matches the Phase 3 ladder).
  Tab links in Clarifications/Improvements preserve the URL scope
  so status flips don't drop the pill. `ApiBucket` client type
  gained optional `scope_kind` / `source_kind` / `repo_id` / etc.
  to match the backend `BucketOut` shape. One new Playwright sweep
  asserts the pill on all four surfaces.
- **2026-04-21** — Phase 4a shipped: first user-visible UI slice.
  New `ScopePill` client component in the AppShell header (mounted
  via optional `scopePill` prop so pages that don't care keep the
  same chrome); `resolveScopeFromSearch` helper for Server Components
  to mirror the pill state. Typed client helpers `listResolvedBuckets`
  and `listBucketArticles` added next to the existing knowledge
  fetchers. `/knowledge` page rewired: workspace scope keeps the
  legacy `.ship/knowledge/` markdown grid; repo/user scope reads
  from the Phase 3 resolver and renders `effective=true` rows.
  Project scope is plumbed through the URL but hidden until the
  backend projects API ships. 2 new Playwright assertions on the
  pill's visibility + URL fallback. Console `tsc` + `next lint` +
  `next build` all green.
