# Agent setup (C12)

This is the operator guide for the real agent that ships with C12.
It covers LLM vendor choice, environment variables, the knowledge
base indexer, and the cost / observability knobs. If you want the
design rationale, read the [feature entry in the pilot plan](./pilot-plan.md#c-conversational-surface)
first; this doc is the "how do I turn it on" companion.

## 1. LLM vendor

Ship supports OpenAI and Anthropic as equal first-class vendors.
Pick one by setting:

```bash
# OpenAI (default)
SHIP_AGENT_VENDOR=openai
SHIP_OPENAI_API_KEY=sk-...
SHIP_AGENT_MODEL_MAIN=gpt-4o          # defaults to gpt-4o
SHIP_AGENT_MODEL_FAST=gpt-4o-mini     # used for the topic classifier

# Anthropic
SHIP_AGENT_VENDOR=anthropic
SHIP_ANTHROPIC_API_KEY=sk-ant-...
SHIP_AGENT_MODEL_MAIN=claude-sonnet-4-5-20250929
SHIP_AGENT_MODEL_FAST=claude-haiku-4-5-20250929
```

> **Pilot note.** The console shows "Agent not configured" (HTTP 412)
> when neither key is present. That's deliberate — we don't want to
> silently degrade to a stub the way the old C10 chat did.

Both vendors use the same `AgentClient` protocol
(`backend/app/services/agent/client.py`); switching is a config
flip, no code change required.

## 2. Embeddings

The knowledge-base tools (`search_repo_kb`, `search_buckets`) rely
on OpenAI's `text-embedding-3-small` (1536 dim). That's hard-coded
for now because pgvector columns in migration `0010_agent_v2` are
declared at 1536 — changing the model is a re-embed pass, not a
schema change. `SHIP_OPENAI_API_KEY` is therefore required even when
the main agent is Anthropic; if you don't want OpenAI billing at
all, disable the KB tools with `SHIP_AGENT_DISABLE_KB_TOOLS=true`
(not yet wired — tracked in Post-pilot P1).

## 3. Knowledge base (`.ship/knowledge/**/*.md`)

Any markdown file under `.ship/knowledge/` in a tenant's repo is
eligible for RAG. The indexer:

1. Runs on `push` webhook events that touch the `.ship/knowledge`
   prefix on the repo's default branch
   (`backend/app/api/v1/routes/github_app.py::_apply_push_event_for_kb`).
2. Can also be triggered manually via
   `POST /v1/repos/{repo_id}/kb/reindex` (bearer token of a
   workspace admin).
3. Chunks markdown into ~800-char pieces with overlap, SHA-diffs
   each chunk so unchanged content is a no-op re-run, and embeds
   only what changed (`backend/app/services/agent/kb_indexer.py`).

The chunks land in `kb_chunks` with one row per `(repo, source_path, chunk_index)`.
HNSW index on the embedding column keeps retrieval in the
single-digit millisecond range for tenants under a few thousand
chunks, which covers every pilot customer we project.

## 4. Memory (named knowledge buckets)

Buckets live in `knowledge_buckets` + `bucket_summaries` and are
user-curated: each one has a slug (stable handle), a name
(editable), and a count of packed summaries. A summary is the
natural-language rollup the agent produces when a thread gets
"packed" — explicitly via `POST /chat/threads/{id}/pack`, or
implicitly when the user accepts the topic-shift banner.

Retrieval is vector-similarity against the summaries' embeddings;
the top 3 hits are injected into the system prompt for every turn.
If you want to give the agent a permanent "who you are" paragraph,
create a bucket named `workspace-intro` and pin a summary into it
manually — there's no CLI for that yet; open a SQL shell or use
the `POST /buckets` + `POST /threads/{id}/pack` endpoints in
sequence.

## 5. Cost guards

The SSE route (`backend/app/api/v1/routes/chat.py::_run_agent_turn`)
enforces two defenses:

- **Per-turn token budget** — `SHIP_AGENT_MAX_TOKENS_PER_TURN`
  (default 32_000). Cumulative across the tool-use loop, not per
  model call; crossing it emits an `error` SSE event and closes
  the turn with `finish_reason=length`.
- **Tool-loop cap** — hard-coded at 8 iterations. A tool that keeps
  calling itself recursively would otherwise loop forever; 8 is
  enough for "search → read two files → file ticket" and no more.

Neither limit prevents a malicious tool from returning a huge
string — that's why the tools themselves cap their output size
(`get_repo_file` refuses blobs > 256 KiB, `list_code_map` returns
top-N only, …).

## 6. Observability

When `SENTRY_DSN` is set, each `astream` round-trip inside the
tool-use loop is wrapped in a `ai.chat` span carrying:

- `loop_index` — which tool-use iteration this is
- `vendor`, `model` — what we actually called
- `tool_count`, `message_count` — prompt shape
- `tokens.prompt_tokens` / `completion_tokens` / `total_tokens` —
  copied straight from the upstream `End` event
- `finish_reason` — `stop` / `length` / `tool_calls` / …

The spans are cheap (no extra IO) and give us an answer to "why
does turn X cost $0.12?" without grepping upstream invoice lines.

## 7. Local development

Minimal `.env.local` for running the agent end-to-end against a
dev backend:

```bash
SHIP_OPENAI_API_KEY=sk-...
SHIP_AGENT_VENDOR=openai
# Everything else optional — sensible defaults in backend/app/core/config.py
```

For the console, the existing Next.js dev server automatically
proxies `/api/chat/stream` to the backend; no extra config.

## 8. Failure modes to watch

- **Embeddings dimension drift** — switching models without a
  `DELETE FROM kb_chunks` followed by a manual reindex will leave
  mismatched vectors. The HNSW index doesn't care, but retrieval
  quality will tank silently. If you change embedding models, run
  `POST /repos/{id}/kb/reindex` for each activated repo.
- **Webhook KB reindex flood** — every push that touches
  `.ship/knowledge` triggers a reindex. For monorepos with many
  authors, consider moving heavy doc edits onto a branch and
  merging in batches rather than pushing every commit.
- **Bucket inflation** — nothing prevents a user from creating
  hundreds of buckets with near-identical semantics. If this
  becomes a problem, expose the "merge bucket" endpoint that's
  stubbed out in `TopicService` (not yet user-facing).
