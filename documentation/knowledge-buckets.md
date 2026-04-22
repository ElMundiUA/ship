# Knowledge buckets

A **knowledge bucket** is a scoped, named collection of retrievable
articles the agent can consult at render time. Patterns are *methods*;
buckets are the *content* those methods look up when they need
project-specific facts (code style, brand rules, API contracts, prior
incidents). Buckets close the gap between "the pattern is universal"
and "the answer is specific to this repo".

The end-to-end pipeline — storage, inbound adapters, resolver, console
surface — landed across Phases 1–8 in April 2026. This page is the
operator's reference; the authoritative schema lives in
`backend/app/db/models/agent_memory.py` and the service code under
`backend/app/services/`.

## The model

- **Bucket** (`knowledge_buckets` row) — a named, scoped container
  (`slug`, `name`, optional description) bound to a workspace and to
  one scope carrier.
- **Article** (`bucket_articles` row) — a single retrievable chunk
  with `title`, `body_md`, `version`, `status`, `provenance`, and an
  embedding for vector search. One bucket may carry many articles; one
  slug is published at a time.
- **Scope** — one of `workspace | project | repo | user`. Declared
  as `scope_kind` on the bucket and enforced by
  `ck_knowledge_buckets_scope_carrier` in the DB (exactly one scope
  carrier FK is non-null per scope).
- **Source** — how the bucket obtains its content. `source_kind` is
  one of `agent_memory`, `repo_files`, `external_static`,
  `connector_proxy`, `audio_transcript`.
- **Topic** — the join key between a pattern and a bucket. Patterns
  declare `spec.knowledge_topics: [...]` (see
  [RFC-0008 § Metadata schema](/docs/protocol/rfc-0008-catalog-reform#metadata-schema));
  buckets carry topic tags on their articles.

### Scopes at a glance

| Scope        | Visibility                                | Typical use                                                       |
|--------------|-------------------------------------------|-------------------------------------------------------------------|
| `workspace`  | Every member of the workspace             | Org-wide rules: brand, tone, security policy                      |
| `project`    | Every repo grouped under the project      | Cross-repo conventions: shared API contracts, design system       |
| `repo`       | One repo                                  | `.ship/knowledge/*.md` mirrors: code style, runbooks              |
| `user`       | The signed-in user only (private)         | Per-user memory; Phase 8 privacy guards on read and write         |

The resolver (`backend/app/services/agent/topic.py`) walks the ladder
most-specific-first: repo shadows project shadows workspace; the
`user` overlay sits alongside as a private layer for the signed-in
user. See `backend/app/db/models/agent_memory.py:BucketScope` for the
authoritative enum.

## On-disk contract: `.ship/knowledge/`

The simplest way to land content is to commit Markdown under
`.ship/knowledge/*.md` in an activated repo. The backend mirrors each
file into a `source_kind='repo_files'`, `scope_kind='repo'` bucket via
`backend/app/services/bucket_repo_files_sync.py`, keyed on the file's
basename (minus `.md`). Mirroring fires on push-webhook, on first repo
activation, and on the `POST /repos/{id}/kb/reindex` admin call.

Today's on-disk shape is deliberately minimal — plain Markdown, H1
taken as the bucket title, body is the single article (slug `"main"`,
version bumps on edit). Structured frontmatter on knowledge files is
not yet read; for the authoritative per-field contract see
`backend/app/services/bucket_repo_files_sync.py` (Phase 5a scope
comment explains the single-article-per-file rule and lists what the
row carries — path, `content_sha`, branch, excerpt).

Git is canonical:

- **Edits** bump the article version; the old row flips to
  `superseded`.
- **Deletes** set `archived_at` on the bucket row — nothing is
  hard-dropped, so downstream citations stay resolvable.
- **Idempotency.** Re-running sync with the same SHA is a no-op, so a
  webhook that touches 99 unrelated files doesn't rewrite 100 rows.

The CLI entry point is `shipctl knowledge` (see
`cli/lib/commands/knowledge.mjs`). Today it ships one subcommand —
`shipctl knowledge init` — which posts to the backend's `knowledge_seed`
endpoint and opens a PR dropping `code-style.md` + `ui-runbook.md`
starters under `.ship/knowledge/`. The starter list is held in lockstep
with `backend.app.services.catalog.KNOWLEDGE_STARTERS`.

## The Distiller

The Distiller is the LLM-backed classifier at
`backend/app/services/distiller.py`. It turns raw inbound content into
`bucket_articles` rows with topic + scope tags. Entry points:

| Endpoint                                                   | Phase | Purpose                                                              |
|------------------------------------------------------------|-------|----------------------------------------------------------------------|
| `POST /v1/workspaces/{ws}/buckets/{slug}/distill`           | 6a    | Run the classifier against a pending source payload.                 |
| `POST /v1/workspaces/{ws}/buckets/{slug}/upload`            | 6c    | External-static upload (file or paste); routes into the classifier.  |
| `POST /v1/workspaces/{ws}/buckets/{slug}/sync`              | 7b    | Connector-proxy sync (refetches the upstream + runs classifier).     |
| `GET  /v1/workspaces/{ws}/buckets/{slug}/distill/runs`      | 6a    | Run history, rendered on the bucket detail page.                     |

Inbound adapters (`backend/app/services/distiller_sources.py`):

- **`.ship/knowledge/*.md` repo mirror** — Phase 2; the `repo_files`
  source.
- **External-static upload** — Phase 6c; the console's "Upload" card
  on the bucket detail page.
- **Notion connector** — Phase 7c;
  `backend/app/integrations/` fetcher projects pages into a
  `connector_proxy` bucket.
- **Linear connector** — Phase 7c; same shape, issues as articles.
- **Agent memory** — packed chat summaries from the Navigator,
  routed through `TopicService.pack_topic`.
- **Audio transcript** — interview transcripts ingested into articles.

Every adapter routes through the same `run_distiller(...)` contract, so
topic / scope tagging and staging semantics stay identical across
sources.

## Console surface

- `/knowledge` (`console/src/app/knowledge/page.tsx`) — scope-aware
  bucket list. The scope pill in AppShell (Phase 4a) switches the
  active context and the page re-renders with the resolver's output
  for the chosen scope.
- **Bucket detail** — article list (reads from `bucket_articles`),
  upload surface (Phase 7a), connector-proxy create/sync surface
  (Phase 7b), Distiller run history.
- **Scope pill propagation** (Phase 4b) — the current scope travels to
  Catalog, Clarifications, Improvements, and the Navigator so every
  surface agrees on "what the user is currently inside".

## Per-user memory

Phase 8 added a `scope_kind='user'` bucket per authenticated user.
Privacy guards on the read path make sure a user only sees their own
`user`-scoped articles; the write path refuses to land a `user` row
under someone else's id. The overlay is parallel to the team ladder —
team reads still walk `workspace → project → repo`, and the `user`
layer adds private notes on top for the signed-in session. See
`backend/tests/test_v1_user_memory_bucket.py` for the enforced
semantics.

## Pattern ↔ bucket wiring

Two entry points for a pattern to consult a bucket:

- **Declarative.** `spec.knowledge_topics: [...]` in the pattern's
  frontmatter. Normative declaration in
  [RFC-0008](/docs/protocol/rfc-0008-catalog-reform#metadata-schema).
  At render time the resolver walks the scope ladder and returns the
  most-specific article per topic.
- **Imperative.** Agent tools exposed by
  `backend/app/services/agent/tools.py`:
  - `list_buckets` — enumerate buckets visible to the current scope.
  - `search_buckets` — vector search over `bucket_articles` (Phase 5d
    reads from the articles table; previously read from
    `bucket_summaries`).
  - `get_knowledge_bucket` — fetch one bucket by slug with its
    articles.

The agent picks whichever entry point matches its context; the
resolver + privacy guards are the same for both.

## Harvesting, staging, drift

Three editorial rules the Distiller + mirror pipelines enforce, shared
with the [Pattern vs knowledge](/docs/authoring/pattern-vs-knowledge)
rubric:

1. **Staged, not active.** New articles land as `status='draft'` on
   the Distiller's staging side. Publishing flips the row to
   `status='published'` and supersedes the previous version. Archived
   rows stay resolvable for citations.
2. **Provenance, then drift.** Every article carries a `provenance`
   JSON blob (source adapter, upstream id, fetched-at) and a
   `content_sha`. `shipctl verify` flags drift between a repo-mirrored
   bucket and the file on disk so buckets don't silently fork from the
   living docs.
3. **Human in the loop.** The Phase 6b LLM classifier proposes
   topic / scope; the operator confirms before articles become
   visible to agents. No silent enforcement promotions, ever.

## Where to next

- The editorial rubric (is this a pattern or a bucket?):
  [Pattern vs knowledge](/docs/authoring/pattern-vs-knowledge).
- `.ship/knowledge/*.md` and `shipctl knowledge`:
  [Configuration](/docs/configuration) and the CLI reference.
- The normative pattern frontmatter shape
  (`spec.knowledge_topics`, `spec.include`, `spec.modes`):
  [RFC-0008](/docs/protocol/rfc-0008-catalog-reform).
- Authoring a pattern that consults buckets:
  [Authoring artifacts](/docs/authoring).
