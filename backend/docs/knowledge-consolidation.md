# Knowledge buckets consolidation — plan

**Status:** Phase 1 landed (`a7c8edd`), Phase 2 in progress.
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

### Phase 4 — Scope pill (frontend, first visible slice)

AppShell top bar gets a universal `scope-pill` with 5 levels
(`Everything / Workspace / <Project> / <Repo> / Mine`). URL + cookie
persisted. First consumers: Knowledge list, Catalog list. Later:
Clarifications, Improvements, Navigator (context prefilter).

### Phase 5 — Article table

New `bucket_articles` (`id, bucket_id, slug, title, body_md, version,
supersedes_id, provenance jsonb, status, embedding`). Migrate
`BucketSummary` into it (preserving thread_id in provenance). Repo
files become articles with `body_md` pulled by the indexer. All
downstream reads (agent tool `get_knowledge_bucket`, console detail
page) switch to articles.

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
