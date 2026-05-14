"""E17 — Navigator memory layer (mem0 + per-message extraction).

One-shot scaffolder for the E17 epic in Ship-on-Ship Linear. Creates
the project + six sub-tickets covering storage, extraction, retrieval,
the planning-flow fix, bucket-memory deprecation, and tests.
Idempotent — re-runs detect by name and skip what exists.

Usage:
    DATABASE_URL=... ENCRYPTION_KEY=... python tools/scripts/create_e17_navigator_memory_project.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.security.encryption import safe_decrypt
from backend.app.integrations.linear.tracker_adapter import LinearTracker


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"

PROJECT_NAME = "E17 — Navigator memory (mem0 + per-message extraction)"

PROJECT_DESCRIPTION = (
    "Replace bucket-based chat memory with mem0 (per-message fact "
    "extraction, retrieval on session start + after 30min gap). "
    "Fixes the sticky drafting-intent bug + the empty-bucket trap "
    "that made Navigator forget every prior conversation."
)

PROJECT_BODY = """\
Swap Navigator's chat memory layer from the bucket-based system
(``KnowledgeBucket`` + ``pack_topic`` on explicit save) to **mem0**
with per-user-message extraction (variant V), retrieval on session
start + after 30-min gap, and a proper exit from drafting mode after
``create_project``.

## Why the current setup forgets

Three independent breakages compound:

1. **Idle-archive cron never packs threads.** The Wave-C sweeper
   flips ``status: active → archived`` after 7 days, but ``pack_topic``
   is only triggered by an explicit "Save to memory" click or the
   topic-shift banner. So archived threads vanish from Navigator's
   memory — the bucket stays empty for 90% of conversations.
2. **``intent='shape_project'`` is sticky.** After ``create_project``
   succeeds the thread STAYS in drafting mode; Navigator keeps trying
   to shape another project on the next turn instead of pivoting to
   "discuss the just-created project".
3. **Per-turn ``retrieve_buckets`` calls are wasted.** Even if buckets
   had content, calling the retriever on every turn is expensive in
   embedding cost + prompt tokens; the agent should fetch once per
   session and call a ``recall`` tool when the topic drifts.

## Architecture

- **mem0 SDK** (``pip install mem0ai``), self-hosted, PGVector backend
  on Ship's existing Postgres. No new infra.
- **Per-user scope** — facts are owned by a single user; workspace
  admins do not see them. Boundary enforced by
  ``WHERE owner_user_id = current_user.id`` + workspace_id filter
  (one user can be in multiple workspaces, facts split per workspace).
- **Variant V extraction**: ``mem0.add()`` fires after every
  meaningful user message (len ≥ 30 chars), fire-and-forget asyncio
  task. Each fact carries ``message_range = [N, N]`` provenance —
  one fact = one source message position, fetchable for ±5 context.
- **Smart retrieval triggers**:
  - First message of a new session → ``mem0.search()``, inject as
    ``{{MEMORY_CONTEXT}}`` in system prompt.
  - Resumed session after 30+ min gap → re-fetch.
  - Mid-conversation: agent calls ``recall(query)`` tool when it
    needs to dig deeper.
- **Project-scope boost**: when the thread is about a known project
  (``intent='shape_project'`` or a project pin), mem0 filters by
  ``project_id`` to surface drafting context.

## Trigger map (current → new)

| Trigger | Now | After E17 |
|---|---|---|
| Page load | Resolve thread, no memory call | No change (lazy) |
| First user message | ``retrieve_buckets`` (empty) | ``mem0.search`` + inject |
| Per user message | per-turn retrieve | ``mem0.add`` fire-and-forget; no retrieve |
| Gap >30min | nothing | re-fetch ``mem0.search`` |
| Topic shift banner | pack outgoing thread | Just archive (facts already in mem0) |
| "Save to memory" button | ``pack_topic`` | Deprecate (or "force re-extract") |
| Idle 7-day archive | status flip, NO pack | Status flip (facts already in mem0) |
| ``create_project`` tool success | nothing | Reset ``intent=None`` + tag mem0 facts with ``project_id`` |
| Tool ``recall`` | doesn't exist | NEW — agent calls when drifting |

## Decisions locked

- mem0 Python SDK with PGVector backend (no separate server).
- LLM for extraction: ``gpt-4o-mini``; embedder: ``text-embedding-3-small``.
- Personal scope only — workspace admin does NOT see another user's facts.
- Hard delete with ``navigator.memory.deleted`` audit row.
- Backfill existing ``my-memory`` bucket articles → ``mem0.add()``.
- Project-scope boost via mem0 filters.
- Refetch threshold = 30 min since ``last_user_activity_at``.
- Backfill runs at night, token cost monitored.

## Out of scope

- Workspace-shared facts (team knowledge stays in the existing
  ``KnowledgeBucket`` system, but not via chat).
- Cross-tracker memory (mem0 facts are workspace-scoped; switching
  trackers in a workspace is a separate concern).
"""


TICKETS: list[tuple[str, str]] = [
    (
        "E17-1: storage + mem0 SDK integration",
        """\
**Goal.** Stand up the mem0 storage layer on Ship's existing
Postgres+pgvector. No new infra; no new service container.

**Scope.**
- Migration ``0070_navigator_memories``:
  ```
  id, workspace_id (FK), owner_user_id (FK), fact_text, embedding vector(1536),
  source_thread_id, source_message_id, source_message_position,
  project_native_id, intent_at_capture, mem0_id (unique),
  confidence, created_at, updated_at
  ```
  + ivfflat index on ``embedding``, btree on ``(owner_user_id,
  workspace_id)``, btree on ``source_thread_id``.
- ``pip install mem0ai`` added to ``requirements-backend.txt``.
- ``apps/backend/app/services/agent/memory.py`` — thin wrapper:
  - ``add(message_body, user_id, workspace_id, metadata)`` — async
    fire-and-forget mem0 call, mirrors result to our table.
  - ``search(query, user_id, workspace_id, filters=None, limit=10)``.
  - ``delete(memory_id, audit_actor_id)`` — hard delete + audit row.
  - ``list_for_user(user_id, workspace_id, filters, page)``.
- mem0 config: LLM = ``gpt-4o-mini``, embedder = ``text-embedding-3-small``,
  vector store = pgvector on the same DB.

**Acceptance.**
- Migration up/down clean.
- Unit test: ``add → search → delete`` round-trip per-user isolation
  (user A's facts invisible to user B).
- Workspace boundary: same user across two workspaces sees only the
  facts from the current workspace.
""",
    ),
    (
        "E17-2: extraction on every meaningful user message (variant V)",
        """\
**Goal.** Per-user-message fact extraction with one-message
provenance. Implements the variant V we settled on — each factoid
hard-linked to ``message_range = [N, N]``.

**Scope.**
- In ``apps/backend/app/api/v1/routes/chat.py`` after the user
  message is persisted:
  ```python
  if memory_enabled(thread) and len(user_msg.body) >= 30:
      asyncio.create_task(memory.add(
          message=user_msg.body,
          user_id=auth.user.id,
          workspace_id=workspace_id,
          metadata={
              "thread_id": str(thread.id),
              "message_id": str(user_msg.id),
              "message_position": user_msg.position,
              "intent": thread.intent,
              "project_id": _infer_project_hint(thread),
          },
      ))
  ```
- Pre-filter: len < 30 chars OR contains only "ok"/"yes"/"да"/"ага"
  patterns → skip the extraction.
- Toggle: new column ``chat_threads.memory_enabled bool default true``.
  Console gets a "Pause memory for this chat" button.
- Errors from mem0 are logged but never block the response — chat
  turn cost stays the same as today on the happy path.
- Backfill script ``tools/scripts/extract_navigator_memories_backfill.py``:
  walk all archived threads, iterate user messages, call ``memory.add``
  in batches with rate-limit + token budget. Designed to run at night,
  prints token spend per workspace.

**Acceptance.**
- Send 5 messages in a test thread, mem0 has ≥ 1 fact per
  meaningful message (3 chars "ok" message NOT captured).
- mem0 add failure (force a network 500 mock) → chat response time
  unchanged + audit row ``navigator.memory.add_failed``.
- Backfill against ws=denys-99938640 catches all archived threads
  without OOM / rate-limit storm.
""",
    ),
    (
        "E17-3: retrieval injection on session start + after 30-min gap",
        """\
**Goal.** Replace the per-turn ``retrieve_buckets`` call with smart
triggers: first message of a session OR re-engagement after 30 min
of silence. Add a ``recall`` tool so the agent can dig mid-thread.

**Scope.**
- In ``apps/backend/app/services/agent/topic.py::assemble_messages``:
  drop the ``retrieved_buckets`` parameter; replace with
  ``retrieved_facts`` from mem0.
- In ``chat.py`` turn handler, decide whether to refetch:
  ```python
  needs_refetch = (
      not prior_messages
      or _seconds_since_last_activity(thread) > 30 * 60
  )
  if needs_refetch and memory_enabled(thread):
      facts = await memory.search(
          query=user_msg.body,
          user_id=auth.user.id,
          workspace_id=workspace_id,
          filters={"project_id": _infer_project_hint(thread)},
          limit=10,
      )
      thread.last_retrieved_facts = [f.id for f in facts]  # JSONB column
  ```
- New JSONB column ``chat_threads.last_retrieved_facts`` so re-renders
  can show "Using N memories" without a re-search.
- Inject ``{{MEMORY_CONTEXT}}`` system message via
  ``_format_memory_context(facts)``.
- New tool ``recall(query: str, limit: int = 10)`` in
  ``apps/backend/app/services/agent/tools.py``. Returns top-N facts
  with ``id`` so the agent can chain into ``recall_context(fact_id)``
  for ±5 source-message neighbourhood.
- Update ``apps/backend/app/resources/agent_roles/navigator.md``:
  - Tell the agent about the prefetched ``{{MEMORY_CONTEXT}}``.
  - Tell it to call ``recall`` when the conversation drifts to a
    new topic (don't ask the user something memory likely answers).
  - Tell it about ``recall_context`` for source spelunking.

**Acceptance.**
- New thread, first turn → mem0.search fires; subsequent turns in
  the same thread within 30 min → NO additional mem0.search.
- Same thread re-opened after 35 min of silence → mem0.search fires
  again.
- Agent invokes ``recall`` when the user asks about something not in
  the prefetched batch (verified in unit test against a seeded mem0
  state).
""",
    ),
    (
        "E17-4: planning flow fix — reset intent + project-tagged facts",
        """\
**Goal.** Close the "sticky drafting mode" hole that makes
``shape_project`` chats unable to exit. Tag drafting-time facts with
the just-created project id so future conversations about that
project surface the brief.

**Scope.**
- In ``apps/backend/app/services/agent/tools.py::_tool_project_create``,
  after the ``tracker.create_project`` call returns successfully:
  ```python
  if self._thread and self._thread.intent == "shape_project":
      self._thread.intent = None
      await self._session.flush()

  await memory.add(
      message=f"Drafted project '{name}': {body[:500]}",
      user_id=self._user_id,
      workspace_id=self._workspace_id,
      metadata={
          "thread_id": str(self._thread.id),
          "project_id": project_native_id,
          "intent_at_capture": "shape_project",
          "kind": "project_brief_drafted",
      },
  )
  ```
- Update ``_PROJECT_DRAFTING_MODE_PROMPT`` in
  ``topic.py``: add explicit instruction that after a successful
  ``create_project``, the agent must congratulate briefly and offer
  EITHER continuing on planning details for the just-created project
  OR ending the session. Do NOT shape another project unless the
  user asks. (Belt-and-suspenders alongside the server-side
  ``intent = None`` reset — the prompt change covers cases where
  the reset somehow doesn't take.)

**Acceptance.**
- E2E test: thread starts with ``intent='shape_project'`` →
  Navigator calls ``create_project`` (mocked) → next turn,
  ``thread.intent`` is NULL + the prompt no longer carries the
  drafting-mode system message.
- A second chat thread that asks about the just-created project's
  title retrieves the ``project_brief_drafted`` fact via
  ``mem0.search``.
""",
    ),
    (
        "E17-5: deprecate bucket-memory for chat + console /memory UI",
        """\
**Goal.** Stop the parallel write to ``KnowledgeBucket`` from chat
flows. Migrate existing ``my-memory`` bucket articles into mem0 as
a one-shot backfill. Ship a Console page so users can audit/edit/
forget their facts.

**Scope.**
- Remove ``retrieve_buckets`` call from ``chat.py`` turn loop.
- Remove ``pack_topic`` call from ``POST /chat/active/new`` (the
  topic-shift banner accept path) and from
  ``POST /chat/threads/{id}/save-to-memory``.
- Leave ``KnowledgeBucket`` model alone — other source_kinds
  (repo_files, knowledge_starters) still use it. Just stop the
  chat-side writes.
- One-shot ``tools/scripts/migrate_bucket_memory_to_mem0.py``:
  walk every ``BucketArticle`` where ``bucket.source_kind ==
  agent_memory AND bucket.scope == user``, call ``memory.add`` with
  the article body + source thread context.
- Console ``/memory`` page (Next.js):
  - List facts (only the current user's), grouped by project /
    untagged.
  - Edit fact text inline.
  - Hard delete with confirm — writes ``navigator.memory.deleted``
    audit row carrying the full original text + source pointer.
  - "Show source" → opens the original chat thread scrolled to the
    ``source_message_id``.
  - Bulk "forget last N days" filter.

**Acceptance.**
- After backfill, ``my-memory`` bucket article count = mem0 fact
  count per user.
- New chat: a fact added via ``memory.add`` appears on
  ``/memory`` within 2 sec.
- Delete via ``/memory`` → fact gone from mem0 + audit row recorded.

**Depends on:** E17-2 (backfill needs the add path live).
""",
    ),
    (
        "E17-6: tests + observability",
        """\
**Goal.** Wire metrics + e2e coverage so we can see if the new path
actually helps before the bucket fallback is retired.

**Scope.**
- Unit tests:
  - per-user isolation (already in E17-1, expand)
  - workspace boundary
  - per-message extraction with metadata round-trip
  - retrieval triggers (first-turn / gap-resume / no-refetch within 30min)
  - ``create_project`` resets intent + writes project-tagged fact
  - ``recall`` tool returns expected hits
  - delete hard-removes + audits
- Integration test against a seeded mem0 state: full session round
  trip (start, 5 messages, 30-min gap simulated, recall fires fresh).
- Metrics surfaced via ``audit_log`` + a new
  ``navigator.memory.search`` audit row carrying
  ``{user_id, latency_ms, hit_count, top_similarity}``.
- Dashboard tile / Inbox alert when:
  - mem0.add failure rate > 5% / hour
  - mem0.search returns 0 hits for 50%+ of refetches (signals empty
    memory for some users → backfill gap)

**Acceptance.**
- Test suite added; coverage on ``memory.py`` ≥ 80%.
- One operator-visible metric in the Console for "memory health"
  (facts/user, search hit rate, recent add failures).
""",
    ),
]


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("ERROR: DATABASE_URL / DB_URL not set in env", file=sys.stderr)
        return 2
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(db_url)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    db_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    connect_args: dict = {}
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = True

    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        result = await session.execute(
            text(
                """
                SELECT nic.secret_ciphertext
                FROM native_integration_installations nii
                JOIN native_integration_credentials nic
                    ON nic.installation_id = nii.id
                WHERE nii.workspace_id = :ws
                  AND nii.provider = 'linear'
                  AND nic.kind = 'access_token'
                  AND nic.revoked_at IS NULL
                ORDER BY nic.updated_at DESC
                LIMIT 1
                """
            ),
            {"ws": SHIP_ON_SHIP_WS},
        )
        ct = result.scalar_one_or_none()
        if ct is None:
            print(
                "ERROR: no native_integration_credentials access_token for "
                "Ship-on-Ship Linear",
                file=sys.stderr,
            )
            return 3
        token = safe_decrypt(bytes(ct))
        if not token:
            print("ERROR: access_token decrypted to empty", file=sys.stderr)
            return 4

    tracker = LinearTracker(access_token=token, team_id=ELS_TEAM_ID)

    existing = await tracker.list_projects(limit=50, query=PROJECT_NAME)
    project = next(
        (p for p in existing if (p.get("name") or "").strip() == PROJECT_NAME),
        None,
    )
    if project:
        print(f"reuse project: {project['name']}  id={project['id']}")
        project_id = project["id"]
        project_url = project.get("url") or ""
    else:
        created = await tracker.create_project(
            name=PROJECT_NAME,
            description=PROJECT_DESCRIPTION,
            body=PROJECT_BODY,
        )
        project_id = created["id"]
        project_url = created["url"]
        print(f"created project: {created['name']}  id={project_id}")
        print(f"  url: {project_url}")

    existing_titles: set[str] = set()
    rows = await tracker.list_tickets(state="all", limit=50)
    for r in rows:
        existing_titles.add((r.get("title") or "").strip())

    created_count = 0
    skipped_count = 0
    for title, body in TICKETS:
        if title in existing_titles:
            print(f"  skip (exists): {title}")
            skipped_count += 1
            continue
        ticket = await tracker.create_ticket(
            title=title,
            body=body,
            project_id=project_id,
        )
        print(f"  + {ticket.display_id}  {title}")
        print(f"    {ticket.url}")
        created_count += 1

    print()
    print(f"done. project={project_url}  created={created_count}  skipped={skipped_count}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
