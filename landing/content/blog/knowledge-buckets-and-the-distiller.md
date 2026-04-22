---
title: Knowledge buckets and the Distiller
slug: knowledge-buckets-and-the-distiller
date: 2026-04-21
kicker: Case study
description: Eight phases in one day — a scope ladder, a dual-written articles table, an LLM-backed ingest classifier, Notion and Linear connectors, and a per-user memory bucket that the agent can actually cite. The knowledge layer Ship needed before it could grow a second brain.
reading_time: 11
author: Denys Kuzin
tags: [knowledge, architecture, case-study, build-in-public]
---

On Apr 21 the "knowledge" in Ship stopped being a folder of Markdown files and started being a system.

Eight phases landed between 10:17 and 17:16 local time. The surface the user sees — a scope pill in the header, a bucket detail page, a Notion connector button, a "save to memory" action on a chat thread — is four thin panels. The surface underneath is a scope-aware storage model, a dual-written articles table, an ingest classifier with an LLM in it and a deterministic fallback behind it, and a visibility predicate every read path composes into its query.

This post walks the eight phases in order and says what each one was for.

{{chart: blog-knowledge-phases | Eight phases across one day — Apr 21. Backend in orange, console in green, Distiller in the middle. The thing we shipped is a system, not a feature. }}

## The Markdown folder that pretended to be a database

Up to Apr 20, "knowledge" in Ship meant a folder. Each activated repo had `.ship/knowledge/` with a handful of Markdown files — `code-style.md`, `ui-runbook.md`, whatever the team had decided the agent should read before touching that repo. A `search_repo_kb` tool vectorised the files and served the top hits into context.

It worked until the first user asked a question the folder couldn't answer.

"Which of these files applies when I'm operating the `ui-app` repo but not the `api` repo?" — there was no way to say. Scope was implicit, pinned to whichever repo the agent happened to be inside. Source was implicit, because the file either existed on disk or it didn't. The agent would cite a file from last month's design spike as if it were this week's runbook, because nothing in the pipeline knew the difference.

The deeper problem was the one we had told ourselves wasn't one yet: there was no way for knowledge to come from anywhere other than a file committed to that repo. No Notion import. No per-user memory. No "this policy applies to every repo in the workspace." The folder shape said the answer is always inside this one directory, on this one branch, right now. That was the ceiling.

We spent Apr 20 naming the shape we wanted, and shipped it in eight phases the next day.

## Phase 1 — scope and source are load-bearing

The first commit of the day, at 10:17, was `a7c8edd — feat(buckets): scope + source awareness on knowledge_buckets (Phase 1)`.

It did not ship a feature. It added two columns to the `knowledge_buckets` table and a check constraint that pinned them to each other.

`scope_kind` is one of `workspace | project | repo | user`. That's the whole vocabulary for "where does this knowledge apply." `source_kind` is one of `agent_memory | repo_files | external_static | connector_proxy | audio_transcript`. That's the whole vocabulary for "where did this knowledge come from." A `ck_knowledge_buckets_scope_carrier` check constraint demands the scope's matching FK — `project_id`, `repo_id`, or `user_id` — is non-null when the scope requires it, and null when it doesn't.

Existing rows backfill to `workspace` / `agent_memory`, matching their current semantics. The public CRUD endpoints still only mint workspace-scoped rows on this commit; the other scopes land with the phases that need them. Nothing breaks. Nothing is visibly different in the console.

> Scope and source are the first two columns every later query reads. Getting them into the schema before any code needed them was the cheapest move of the day.

The reason we spent the first commit on enum plumbing instead of on a feature is that every later phase reads these two columns. The resolver walks scope. The retrieval filters by source. The per-user memory endpoint is defined by `scope_kind = 'user'`. Phase 8's privacy guard is a one-line predicate over `scope_kind`. If scope and source had gone in as an afterthought, every phase on top of them would have carried the retrofit tax.

Four partial unique indexes replaced the single workspace-wide unique on `(workspace_id, slug)`. The same slug — `code-style`, say — can now live as a workspace-level bucket, a per-repo bucket on every activated repo, and a per-user bucket on every user, without insert-time collisions. Without it, the second repo to import a `code-style.md` file would 409.

## Phase 2 — mirror the folder that was already there

Twelve minutes later, `f5ea287 — feat(buckets): mirror .ship/knowledge/*.md into knowledge_buckets (Phase 2)`.

Every Markdown file under `.ship/knowledge/` in every activated repo now mirrors into a `scope_kind='repo'`, `source_kind='repo_files'` bucket. The sync is idempotent by vendor SHA. File deletion sets `archived_at`; resurrection clears it. Oversize and binary files are skipped. The sync fires on push webhook, on first repo activation, and on the manual `POST /repos/{id}/kb/reindex` call.

Git stays canonical. The user keeps authoring in Markdown in a branch in a PR. The backend sees every edit as a bucket update.

The reason this is Phase 2 rather than Phase 5 is that existing content had to land in the new shape before anything depending on the new shape could ship. If we had built the resolver first and the mirror second, the resolver would have returned empty for a week. Mirror, then resolve. Never the other way.

## Phase 3 — the scope ladder resolver

At 10:54, `d4a982a — feat(buckets): resolver for scope ladder (Phase 3)` shipped the endpoint that reads the ladder.

`GET /v1/workspaces/{ws}/buckets/resolved` is the read-only projection over the scope × source lattice that every frontend and every agent tool calls to answer "what knowledge applies here". Context carriers — `repo_id`, `project_id` — are opt-in query parameters. The caller's private `user` overlay is always included. Other users' `user`-scoped rows are never returned by this endpoint; the visibility rule is enforced here, explicitly, rather than assumed.

The priority ladder is `workspace(10) ≺ project(20) ≺ repo(30)`, with a parallel `user(40)` overlay that beats repo for the caller. Priorities are 10-spaced on purpose: a future "team" tier between project and repo slots in at `25` without renumbering anything that already exists.

The response carries every candidate bucket with its priority, effective scope, and an `effective: true|false` flag, plus a `winners_by_slug` map for consumers that only want the deduped view. Archived rows are filtered by default, with `include_archived=true` for the "where did my bucket go" debugging case.

One test in this commit looks like defensive paranoia and isn't. The router registers `/buckets/resolved` before `chat.router`, so the literal path wins over `chat`'s `/buckets/{slug}` CRUD. A dedicated test locks the ordering in. The first time someone reshuffles the router imports, the test will fail instead of production.

## Phase 5 — four commits to move one table

Phase 4 is the scope pill in the UI, but it has to wait until the read path it's going to query exists. The backend kept moving.

Phase 5 was the `bucket_articles` dual-write, and it took four commits because dual-writes are four commits if you want them done right.

`0b85a6d — feat(buckets): bucket_articles table + repo_files dual-write (Phase 5a)` added the table. Versioning, supersession, a partial-unique on `(bucket_id, slug) WHERE status='published'`, a provenance JSONB blob, an `archived_at` column. The `repo_files` sync path dual-writes one "main" article per bucket alongside the existing row. Edits bump the version and supersede the previous. Resurrection uses `MAX(version)+1` so the partial unique can't collide with dormant history.

`4b7af5e — feat(buckets): mirror bucket_summaries into bucket_articles (Phase 5b)` did the same dual-write for agent-memory buckets. Every packed chat summary gets a `thread-<uuid>` article. Failures log but don't raise — the pack is canonical and the mirror is shadow. A migration one-shots existing data across in pure SQL.

`9d2c4f3 — feat(buckets): retrieve_buckets reads from bucket_articles (Phase 5c)` is the first read-path cutover. The warmed-memory retriever stops reading summaries and starts reading articles. The new query has a tighter `WHERE`: `status='published'`, `archived_at IS NULL`, `embedding IS NOT NULL`, `source_kind='agent_memory'`. Superseded history no longer leaks.

`64821a6 — feat(buckets): agent tools + bucket detail read from bucket_articles (Phase 5d)` finishes it. `search_buckets`, `get_knowledge_bucket`, and `list_buckets` all project over articles. A new `GET /buckets/{slug}/articles` endpoint returns the canonical shape. The legacy `/summaries` endpoint stays, deprecated, slated for removal in Phase 9.

Four commits for one table. Why.

Because dual-writes are how you move a read path across a table boundary without lying to the caller. Write-only first: both shapes land, both stay consistent, nothing reads the new one. Then the read path flips, one consumer at a time, each in its own reviewable commit. When a commit breaks, rollback is one revert, not a half-landed migration.

> A dual-write you roll out all at once isn't a dual-write. It's a cutover you wrote four commits of scaffolding for and then threw away.

The four-commit shape is textbook. We wrote it as four because rushing the cutover is how stale reads land in production.

## Phase 4 — the scope pill had to stop the UI from lying

Between the Phase 5 dual-write and the Distiller, two small console commits shipped the first user-visible surface.

`d9e106a — feat(console): scope pill in AppShell + scope-aware /knowledge (Phase 4a)` added a `ScopePill` to the app header with three scopes: Workspace, Repo, Me. State is driven by URL search params — `?scope=...&repo_id=...` — so server components can mirror it, bookmarks keep it, and browser history works. A fallback silently flips back to workspace when the `repo_id` doesn't resolve against the activated-repos list.

`df16dba — feat(console): propagate scope pill to catalog/clarifications/improvements/chat (Phase 4b)` threaded the same pill through the other four surfaces users actually live on. Catalog filters by `source_repo_id` when scope is Repo. Clarifications and Improvements filter rows by `row.repo_id`. The Navigator narrows its memory-bucket sidebar. Ambient workspace buckets stay visible under every scope to match the Phase 3 inheritance ladder.

Once the backend knows the answer is scoped, the UI has to stop pretending it isn't. A user who sees "the catalog" in Workspace and "the catalog" in Repo and watches the list change with no way to tell why has been handed a search box with a lossy opinion layer. The pill is the opposite — it makes the scope argument readable on every surface that reads from the ladder.

The pill is not cosmetic. It is the Phase 3 resolver made visible.

## Phase 6 — the Distiller

At 14:31, the ingest layer started.

`POST /v1/workspaces/{ws}/buckets/{slug}/distill` is the single write path every inbound knowledge blob travels through on its way into `bucket_articles`. PR merge banners, external-static uploads, connector-proxy snapshots, audio transcripts — same endpoint, same classifier contract.

`08f8314 — feat(backend): Distiller stub — POST /buckets/{slug}/distill + run history (Phase 6a)` shipped the plumbing with a deterministic stub classifier. The classifier takes a payload and decides one of four things: new article, update existing, skip (empty body), skip (same content SHA). The decision lands as a `distiller_runs` row with status, decision, input_ref, output_refs, timestamps. Every call is audited; every run is listable via `GET /distill/runs`.

The stub is deliberately dumb. The point of 6a was to pin the contract — row shape, decision vocabulary, audit trail — before an LLM touched it.

Twenty-seven minutes later, `f041d5b — feat(backend): LLM-backed Distiller classifier (Phase 6b)` replaced the stub with a fast chat model in JSON mode.

`run_distiller` now accepts a `Classifier` protocol. The stub is renamed `classify_stub`. A new `classify_with_llm` asks the model to decide `new | update | skip` and to propose a slug and title. Every LLM verdict is re-checked against DB reality — a `_reconcile_classification` pass that makes sure the LLM can't mis-supersede a row that doesn't exist, or update a slug that collides with something it can't see. If the LLM errors, the path falls back to the stub. Ingest never blocks on a flaky model.

The endpoint takes a new `classifier` field: `auto | stub | llm`, default `auto`. `auto` picks LLM when an agent key resolves, stub otherwise. `llm` returns 503 when no agent is configured, because a caller who asked for LLM explicitly should hear about it when we can't deliver. Every run records `{name, reasoning, vendor}` under `output_refs.classifier` so an operator can trace any article back to the classifier that admitted it.

A model-backed classifier without a deterministic fallback is a new single point of failure dressed as intelligence. The fallback is cheap — the stub is seven lines — and it means the ingest contract holds regardless of what the model vendor is doing at that moment.

`c8fb5a1 — feat(backend): Distiller inbound adapters + external-static upload route (Phase 6c)` shipped the adapters. A PR-merge hook writes one `pr-<number>` article per merged PR into a repo-scoped `pr-summaries` bucket, idempotent on webhook replay. An `ingest_external_static_upload` function backs a new multipart `POST /upload` route with a 1 MiB cap, strict UTF-8, and a narrow allow-list. An `ingest_connector_page` stub documents the shape for Notion and Linear. An `ensure_bucket` helper does scope-aware get-or-create respecting the Phase 1 uniqueness invariant.

End of Phase 6: one endpoint, one classifier protocol with two implementations, an audit table, four inbound source kinds, zero callers in production.

## Phase 7 — the console connects to ingest

`8fdebd4 — feat(console): Knowledge upload surface on bucket detail (Phase 7a)` rewired `/knowledge/[id]` to fan out the legacy body fetch alongside `getBucket` / `listBucketArticles` / `listDistillerRuns`. A `uploadBucketFileAction` server action validates size and MIME before calling `uploadToBucket`, then revalidates the page. An `upload-card.tsx` holds the file picker, the classifier selector (`auto | stub | llm`), and an inline result banner. An Articles card lists published articles with provenance hints; a Distiller Runs card shows the last twenty.

`3d6bf6b — feat(console): Connector-proxy bucket create + sync surface (Phase 7b)` added a new-bucket dialog with Upload / Connector tabs on `/knowledge`, a `ConnectorCard` and Sync-now button on the detail page, and a `POST /buckets/{slug}/sync` endpoint that drives `ingest_connector_page` with a stub body so the Distiller loop is observable before real fetchers land.

`d160fc5 — feat(backend): Notion connector fetcher (Phase 7c)` dropped the first real fetcher into a new connector registry at `backend/app/services/connectors/`. It fetches a Notion page via `/v1/pages/{id}` and paginated `/v1/blocks/{id}/children`, then renders the blocks to Markdown — headings, bullets, to-dos, quotes, callouts, code, dividers, child pages. Shape validation runs before secret decrypt, so buckets with unsupported resource shapes fall back to the stub body rather than 502'ing.

`c72a52e — feat(backend): Linear issue connector fetcher (Phase 7c)` followed twelve minutes later. Given `resource_ref={issue_id: "ELM-42" | "<uuid>"}` and a Linear integration, the fetcher POSTs one GraphQL query and renders the issue to a deterministic Markdown page. HTTP 401 / 403 and GraphQL auth errors become reconnect hints. A `data.issue=null` response becomes a "not visible to integration" error instead of a silent empty page.

End of Phase 7: Notion pages and Linear issues enter the same pipeline `.ship/knowledge/*.md` files do, under the same `run_distiller(...)` contract.

## Phase 8 — per-user memory, and the visibility predicate

`aeeec74 — feat(backend): per-user memory bucket + privacy guards (Phase 8)` landed at 17:16, the last commit of the day. It is the keystone.

The first file it adds is `backend/app/services/bucket_visibility.py`. The whole file is a single function.

```python
def visible_to_user_clause(
    caller_user_id: uuid.UUID,
) -> ColumnElement[bool]:
    return or_(
        KnowledgeBucket.scope_kind != BucketScope.USER,
        and_(
            KnowledgeBucket.scope_kind == BucketScope.USER,
            KnowledgeBucket.user_id == caller_user_id,
        ),
    )
```

A one-line SQL predicate. True for every non-user-scoped bucket and for the caller's own user-scoped buckets; false otherwise. That's the whole privacy contract for `scope=user` reads.

Every choke point composes it into its existing select. `TopicService.retrieve_buckets` — the warmed-memory retriever that packs agent memory into every chat turn. `ToolBox._tool_search_buckets` — the agent-facing search tool. `GET /buckets` — the listing endpoint. `POST /buckets` checks it before inserting a `scope_kind=user` row with a `user_id` that isn't the caller's, and returns 403 if the caller tried to mint a bucket attributed to a user who can't see it.

If each choke point had rolled its own visibility check, four files would have drifted within a quarter. One helper, imported everywhere, means the privacy semantics are either correct in every caller or incorrect in every caller.

> Admins can't mint a bucket attributed to a user who can't see it. It's a 403 at the API.

The write path refuses to create a record the named user cannot read. There is no admin override. This is not a policy enforced with a comment; it is the API behaviour.

On top of the guards sits the feature users notice. `ensure_user_memory_bucket` is idempotent via the existing `uq_knowledge_buckets_user_slug` partial unique; it pins slug to `my-memory` and source to `agent_memory`. A new `POST /v1/workspaces/{ws}/chat/threads/{id}/save-to-memory` endpoint loads the thread, mints `my-memory` if missing, and calls `pack_topic` into that bucket. The role is `ROLES_READ` — this is your own private bucket, not an admin action. The thread stays active after the save, unlike `/pack` which archives it.

This is the phase that stops the agent from being an amnesiac. Before Phase 8, a useful thing you explained to the agent disappeared when the thread ended. After Phase 8, you hit a button, and the thread becomes an article in a bucket the retriever finds on your next turn, that no other user can see, and that shows up in the Me scope of your pill.

Eleven new tests pin the semantics. The helper's idempotency under the partial-unique race. Per-user uniqueness. The visibility predicate hiding other users' rows. `create_bucket` self-vs-other returning 403. Save-to-memory minting on the first call and being idempotent on the second. Empty thread → 400. Unknown thread → 404.

450 backend tests green. End of day.

## The principle

The scope ladder is the load-bearing concept. Four scopes, sorted on a predictable ladder, with a parallel overlay for the signed-in user — that vocabulary is what turns "a file on disk" into "an answer for this context."

The Distiller is the ingest contract. Every source — repo file, PR merge, external upload, Notion page, Linear issue, audio transcript, packed chat — travels through the same `run_distiller(...)` call, produces the same `distiller_runs` audit row, and lands in the same `bucket_articles` shape. A new source next week is a new adapter and nothing else.

Articles are the retrieval shape. Versioned, published or superseded, with an embedding, a provenance blob, and a partial-unique on `(bucket_id, slug)` that keeps the current article current without destroying the history. Every reader now reads the same table with the same filter.

Per-user memory is the privacy commitment. One visibility predicate composed into every choke point. A 403 at the write side on top of the filter at the read side, so the two agree on who owns what and there is no way to land a row that only one of them can see.

All four had to exist together. Without the ladder, the Distiller has nowhere coherent to write. Without the Distiller, the ladder has nothing to populate it. Without articles, the retriever reads a projection that's out of date for as long as the migration takes. Without the visibility predicate, per-user memory is a footgun.

A knowledge system that can't tell you the scope of its answer is not a knowledge system. It is a search box with a lossy opinion layer, and the lossy layer is the bug. We spent Apr 21 replacing the search box with a system that names its scope, its source, and the user who can see it, and writes every one of those facts down in a place a query can read.
