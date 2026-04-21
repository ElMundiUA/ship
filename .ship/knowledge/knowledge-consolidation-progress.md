# Knowledge buckets consolidation — internal snapshot

One-page snapshot of where the knowledge-buckets consolidation is
today. The full plan with every sub-phase's acceptance criteria,
changelog, and deferred ideas lives in
[`backend/docs/knowledge-consolidation.md`](../../backend/docs/knowledge-consolidation.md) —
this file is the "what's alive on `main` right now" view for
whoever walks into the repo next.

Auto-synced into the Ship console via Phase 2's
`knowledge_kb_sync`, so the same content also appears in
`/knowledge/ship · internal`.

## Shipped on `main` — Phase matrix

| Phase | Name                                       | Commit     | Key artifact                               |
| ----: | ------------------------------------------ | ---------- | ------------------------------------------ |
|   1   | Foundation (schema / migrations)           | `a7c8edd`  | `migrations/0014_bucket_scope_source.py`   |
|   2   | `.ship/knowledge/*.md` → DB buckets        | `f5ea287`  | `services/knowledge_kb_sync.py`            |
|   3   | Resolver (scope ladder)                    | `d4a982a`  | `routes/buckets_resolver.py`               |
|  4a   | Scope pill — AppShell + `/knowledge`       | `d9e106a`  | `console/src/components/scope/*`           |
|  4b   | Scope pill — catalog/clarif/impr/chat      | `df16dba`  | same, extended                             |
|  5a   | `bucket_articles` table + dual-write       | `0b85a6d`  | `migrations/0016_bucket_articles.py`       |
|  5b   | Mirror `bucket_summaries` → articles       | `4b7af5e`  | `services/bucket_summary_articles.py`      |
|  5c   | Retriever cutover to articles              | `9d2c4f3`  | `services/agent/topic.py`                  |
|  5d   | Agent tools + `/articles` read surface     | `64821a6`  | `routes/articles.py`, `agent/tools.py`     |
|  6a   | Distiller stub                             | `08f8314`  | `services/distiller.py`                    |
|  6b   | LLM-backed classifier                      | `f041d5b`  | same, `classifier=auto\|stub\|llm`         |
|  6c   | Inbound adapters (PR / upload / connector) | `c8fb5a1`  | `services/distiller_sources.py`            |
|  7a   | Console upload surface                     | `8fdebd4`  | `console/src/app/knowledge/[slug]/*`       |
|  7b   | Connector-bucket create + sync (stub body) | `3d6bf6b`  | `routes/distiller.py`                      |
|  7c   | Notion connector (real fetch)              | `d160fc5`  | `services/connectors/notion.py`            |
|  7c   | Linear connector (real fetch)              | `c72a52e`  | `services/connectors/linear.py`            |
|   8   | Per-user memory bucket + visibility guards | `aeeec74`  | `services/bucket_visibility.py`            |

Backend consolidation is closed. `bucket_articles` is the sole
read surface; `bucket_summaries` is maintained for write-back
compat only (removal slot is Phase 9c). Frontend scope pill is
live across all feature surfaces.

## What works end-to-end right now

- **Scope-aware sidebar pill** — every feature surface
  (`/knowledge`, `/catalog`, `/clarifications`, `/improvements`,
  `/chat`) filters by the selected scope (workspace / project /
  repo).
- **Distiller** — stub + LLM classifiers, dedup on
  `content_sha`, versioning via `supersedes_id`.
- **Inbound sources**:
  - PR-merged webhook → `pr-summaries` bucket per repo.
  - Console upload → external-static bucket by slug.
  - Connector-proxy buckets with real Notion and Linear
    fetchers in the `backend/app/services/connectors/` registry.
- **Per-user memory** — `scope=user` buckets, auto-mint
  `my-memory` on first `save-to-memory` call, visibility guards
  prevent cross-user leakage at four choke points
  (`retrieve_buckets`, `search_buckets` tool, `list_buckets`,
  `create_bucket`).

## Next up — Phase 9 sub-phases (planned, none started)

- **9a** Sidebar IA — scope-grouped nav
  (`REPO / PROJECT / WORKSPACE / ADMIN / ME`), collapsible,
  reacts to the scope pill.
- **9b** `/me/knowledge` route — private overlay backed by
  Phase 8's visibility helper.
- **9c** Retire `bucket_summaries` — drop dual-write + legacy
  table (migration `0017_drop_bucket_summaries.py`).
- **9d** Retire `KbChunk` / `kb_indexer` — unify the RAG index
  into `bucket_articles` (single biggest LOC reduction left).
- **9e** `/knowledge` off disk — delete `knowledge_lister.py`.
- **9f** Cross-scope global search (optional) — `GET /search` +
  `⌘K` palette.
- **9g** ME overlay — re-home Navigator, add Tasks page.

## Parked (tracked in the main doc)

Eight categories, see the "Out of the initiative" section in
[`backend/docs/knowledge-consolidation.md`](../../backend/docs/knowledge-consolidation.md)
for the full list with rationale:

1. **Connector extensions** — Notion database listing, Notion
   child-block recursion, Notion incremental sync, Linear team
   mirror, Linear comments/attachments, Confluence, Google Docs,
   Slack, GitHub Discussions.
2. **Phase 8 follow-ups** — auto-save consent toggle,
   "forget this", per-user quota, pin important,
   share-with-team, memory TTL.
3. **Retrieval quality** — hybrid BM25+cosine ranker, per-scope
   result mixing, per-workspace threshold tuning, citation
   surface, usage metrics, LLM rerank.
4. **Distiller polish** — manual classifier override, batch
   backfill, cost cap, global dedup, versioning UI.
5. **Observability / ops** — per-connector sync health
   dashboard, rate-limit token bucket, retry/backoff, audit log,
   alerting.
6. **Security / compliance** — GDPR export, offboarding delete
   window, signed upload URLs, secret rotation reminder.
7. **Onboarding / growth** — wizard steps, sample pack,
   save-to-memory nudge.
8. **Cross-cutting** — bucket-level ACLs, cross-workspace
   sharing, i18n, archived threads browser.
