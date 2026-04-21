# Knowledge buckets consolidation — plan

**Status:** Phases 1–3, 4a, 5a, 5b, 5c, 5d landed. Backend consolidation
closed — `bucket_articles` is the sole read surface (retriever, agent
tools, new `/articles` endpoint, `summary_count` on bucket listings);
`bucket_summaries` is maintained for write-back compat only, reads
removed outside the legacy `/summaries` endpoint (deprecated, removal
in Phase 9). Phase 4a shipped the first visible UI slice — scope pill
in AppShell + scope-aware `/knowledge` page. Next: propagate the
pill to Catalog / Clarifications / Improvements / Navigator.
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

Follow-up consumers (tracked separately): Catalog list,
Clarifications, Improvements, Navigator (context prefilter).

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

`POST /v1/buckets/{id}/distill` with an input blob (PR diff, upload,
webhook payload, transcript chunk) → `{ decision: "new" | "update" |
"skip", article_ids, diff }`. Phase 6a ships as a stub that always
creates a new article. Phase 6b plugs in the LLM classifier. Phase 6c
wires in the inbound sources (push-based, not pull).

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

### Phase 8 — User-memory bucket

Per-user bucket (scope='user') auto-created on first sign-in.
Navigator writes to it at end-of-thread with consent. Retrieval uses
it as a private overlay alongside workspace/project/repo buckets.

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
