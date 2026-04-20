"""TopicService — the "memory & topic shift" brain of C12.

Four verbs, one engine:

- :meth:`TopicService.classify_shift` — fast LLM call that decides
  whether the user's latest message is a *topic shift* from the
  currently-active thread's running summary.
- :meth:`TopicService.retrieve_buckets` — vector retrieval of
  :class:`BucketSummary` rows semantically close to the user's
  message. Returns the top-K with their bucket metadata so the UI
  can render a "recall from bucket X" banner.
- :meth:`TopicService.pack_topic` — summarise a thread into a
  :class:`BucketSummary`, embed, and attach to the requested
  bucket. Sets the thread's ``topic_summary`` / ``packed_into_bucket_id``
  so the UI can render the sentinel "packed → bucket" message.
- :meth:`TopicService.assemble_messages` — build the ``ChatMessage``
  array handed to :meth:`AgentClient.astream`. Layers system prompt,
  optionally-retrieved bucket summaries, ``.ship/knowledge`` hints,
  and the live thread history.

Design: this is the service layer between the chat SSE route and the
data layer. The SSE route never touches :class:`KbChunk` or
:class:`BucketSummary` directly; it just asks :class:`TopicService`
"assemble me a turn, here's the new user message" and gets back a
ready-to-stream prompt.

Topic-shift UX notes:

- We only *suggest* a shift; the UI renders a banner the user can
  accept ("Yes, pack and start fresh") or dismiss ("no, keep going").
  Silent auto-packing would be the #1 way to lose context the user
  cared about, so we never do it.
- The classifier is fast-model (``agent_model_fast``) + single-turn,
  and only runs when the thread has at least two user turns — a
  first turn can't be a "shift" from itself.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_memory import (
    BucketSummary,
    KbChunk,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import ChatMessage as ChatMessageRow
from backend.app.db.models.agent_surface import ChatThread
from backend.app.services.agent.client import (
    AgentClient,
    ChatMessage,
)
from backend.app.services.agent.embedding import embed_text


logger = logging.getLogger(__name__)


# The system prompt is intentionally terse: the agent should be
# helpful and grounded in the tools it has. Personality is delegated
# to the user via the buckets they keep — we don't bake a quirky
# "Ship-ling" persona that would age badly.
_AGENT_SYSTEM_PROMPT = (
    "You are Ship, a software-engineering agent living inside a single "
    "chat window. Your job:\n"
    "- answer the user's question with concrete, accurate, concise text,\n"
    "- use tools whenever they'd help, and\n"
    "- when the user asks you to plan or track work, propose it in text "
    "first; open a ticket only on explicit confirmation.\n\n"
    "Tool-picking heuristics:\n"
    "- Need a repo UUID for a follow-up call? Call ``list_activated_repos`` "
    "first instead of asking the user.\n"
    "- Asked about existing tickets / 'what's on my plate' / whether a "
    "ticket already exists? Use ``list_tickets`` before "
    "``create_ticket``.\n"
    "- Asked about a specific pull request's contents, changed files or "
    "how long it was open? Use ``get_pull_request`` (it returns the "
    "timeline and the diff hunks). Add ``include_reviews`` / "
    "``include_commits`` / ``include_comments`` for richer context. "
    "For 'what PRs are open?' use the cheaper ``list_pull_requests``.\n"
    "- Asked 'what happened recently?' / 'what did I miss since X'? "
    "``list_recent_activity`` accepts ``since`` and ``repo_id`` for "
    "scoped history.\n"
    "- Asked what patterns / workflows / collections Ship has, or before "
    "filing ``create_artifact_feedback``? Call ``list_catalog_artifacts`` "
    "so the artifact id is real. Follow up with "
    "``get_catalog_artifact`` to read the full playbook body.\n"
    "- Asked what's connected / why a tracker call failed? "
    "``list_integrations`` shows status and last-health.\n"
    "- Asked about automation lanes / runs / 'did the gate pass?' Use "
    "``list_pipelines``, ``list_pipeline_runs``, ``get_pipeline_run``.\n"
    "- Asked about the agent's open questions or proposals? "
    "``list_clarifications`` / ``list_improvements`` — consult these "
    "*before* proposing something new so you don't re-surface declined "
    "items.\n"
    "- Asked about dashboard KPIs / DORA / success rate / 'how are we "
    "doing?' — ``get_metrics_overview``.\n"
    "- Need a specific slice of a large file? Prefer "
    "``get_repo_file`` with ``start_line`` / ``end_line`` over dumping "
    "the whole blob. For monorepos, narrow ``list_code_map`` with "
    "``path_prefix`` / ``glob`` / ``directories_only``.\n"
    "- Asked 'what do you remember?' with no specific topic? "
    "``list_buckets`` is a flat enumeration; ``search_buckets`` is for "
    "when you already have a topic to search by.\n"
    "- Need a named bucket's packed summaries? ``get_knowledge_bucket`` "
    "by slug.\n"
    "- KB empty or wrong scope? ``list_activated_repos`` includes "
    "``kb_chunk_count`` / ``kb_last_indexed_at``. Narrow semantic KB "
    "search with ``search_repo_kb`` ``path_prefix`` / ``path_glob``; "
    "use ``include_full_content`` when you need more than a short "
    "snippet.\n"
    "- 'Where is ``foo`` defined in code?' — ``search_code`` on a "
    "repo (GitHub code search; rate-limited — don't spam).\n"
    "- Filter tickets: ``list_tickets`` supports ``state``, ``query``, "
    "``assignee_me`` (Linear), ``assignee`` login (GitHub).\n"
    "- Workspace roster / roles? ``list_workspace_members``. Pending "
    "invites? ``list_workspace_invites`` (admin-only). Workspace "
    "catalog toggles / slug? ``get_workspace_settings``. Custom "
    "artifact mirrors? ``list_workspace_artifact_repos``.\n"
    "- 'Who changed setting X?' / security review? ``list_audit_events`` "
    "(admin-only — if the user is not admin, say so).\n"
    "- Before filing catalog feedback, check ``list_artifact_feedback`` "
    "for an open item on the same artifact.\n\n"
    "Hard rules:\n"
    "- Never fabricate repo paths, tickets, URLs, artifact ids, pipeline "
    "ids, or integration names. If you need one, call a tool; if no "
    "tool can get it, say so.\n"
    "- Cite the source when quoting from KB or files (path + chunk).\n"
    "- Stay inside the current topic unless the user changes subject; "
    "the host may suggest topic shifts, you don't need to."
)


@dataclass(slots=True)
class TopicShiftDecision:
    """Structured output from :meth:`TopicService.classify_shift`.

    ``shifted`` is a boolean the UI gates the banner on; ``reason``
    is a short human-readable string we show as a tooltip so the
    user can see *why* the agent thinks they're on a new topic,
    and ``new_title`` is a proposed one-line label for the new
    conversation thread.
    """

    shifted: bool
    reason: str
    new_title: str | None


@dataclass(slots=True)
class BucketHit:
    """One bucket-summary match returned by :meth:`retrieve_buckets`."""

    bucket_id: uuid.UUID
    bucket_slug: str
    bucket_name: str
    summary_id: uuid.UUID
    title: str
    summary: str
    similarity: float


class TopicService:
    """Per-(workspace, user) memory engine for C12 single-window chat."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        client: AgentClient,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._settings = settings
        self._client = client
        self._workspace_id = workspace_id
        self._user_id = user_id

    # ------------------------------------------------------------------
    # 1. Classify topic shift
    # ------------------------------------------------------------------

    async def classify_shift(
        self,
        *,
        running_summary: str | None,
        recent_messages: Sequence[ChatMessageRow],
        new_user_message: str,
    ) -> TopicShiftDecision:
        """Ask the fast model whether this is a topic shift.

        Returns :class:`TopicShiftDecision` with ``shifted=False`` and
        an empty reason when we don't have enough signal to judge
        (brand-new thread, < 2 prior turns), so the UI can skip
        rendering the banner without special-casing.
        """
        prior_user_turns = [m for m in recent_messages if m.role == "user"]
        if len(prior_user_turns) < 1:
            return TopicShiftDecision(shifted=False, reason="", new_title=None)

        # Keep the payload small — we only need the last few turns
        # plus the running summary. The fast model doesn't need the
        # whole thread to tell shifts from continuations.
        trimmed = _last_turns_text(recent_messages, n_turns=6)
        prompt = (
            "You are a topic-shift classifier for a single-window chat. "
            "Decide whether the user's latest message continues the "
            "current topic or opens a different one.\n\n"
            f"Running summary (may be empty): {running_summary or '—'}\n"
            f"Recent turns:\n{trimmed}\n\n"
            f"Latest user message:\n{new_user_message}\n\n"
            "Respond with a single JSON object: "
            '{"shifted": bool, "reason": "short explanation", '
            '"new_title": "one-line title for the new topic or null"}. '
            "No prose outside the JSON."
        )
        try:
            raw = await self._client.acomplete(
                [ChatMessage(role="user", content=prompt)],
                model=self._settings.agent_model_fast,
                max_tokens=256,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("topic-shift classifier failed: %s", exc)
            return TopicShiftDecision(shifted=False, reason="", new_title=None)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Anthropic doesn't support response_format, so payload
            # may have prose around the JSON. Salvage the braces.
            payload = _salvage_json_object(raw)
        shifted = bool(payload.get("shifted"))
        reason = str(payload.get("reason") or "")[:512]
        title = payload.get("new_title")
        if not isinstance(title, str) or not title.strip():
            title = None
        return TopicShiftDecision(shifted=shifted, reason=reason, new_title=title)

    # ------------------------------------------------------------------
    # 2. Retrieve buckets for warmed context
    # ------------------------------------------------------------------

    async def retrieve_buckets(
        self, *, query: str, limit: int = 3, similarity_threshold: float = 0.25
    ) -> list[BucketHit]:
        """Top-K bucket summaries semantically close to ``query``.

        Returns entries whose cosine-similarity clears
        ``similarity_threshold`` — this keeps "no useful memory"
        from polluting the prompt with unrelated summaries. The
        threshold is deliberately permissive (0.25) because
        text-embedding-3-small's cosine distance distribution is
        narrow; callers can tighten via the argument.
        """
        qvec = await embed_text(query, settings=self._settings)
        stmt = (
            select(
                BucketSummary,
                KnowledgeBucket.slug,
                KnowledgeBucket.name,
                BucketSummary.embedding.cosine_distance(qvec).label("dist"),
            )
            .join(
                KnowledgeBucket,
                KnowledgeBucket.id == BucketSummary.bucket_id,
            )
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .where(KnowledgeBucket.archived_at.is_(None))
            .order_by("dist")
            .limit(max(1, min(limit, 10)))
        )
        rows = (await self._session.execute(stmt)).all()
        hits: list[BucketHit] = []
        for summary, slug, name, dist in rows:
            similarity = 1.0 - float(dist)
            if similarity < similarity_threshold:
                continue
            hits.append(
                BucketHit(
                    bucket_id=summary.bucket_id,
                    bucket_slug=slug,
                    bucket_name=name,
                    summary_id=summary.id,
                    title=summary.title,
                    summary=summary.summary,
                    similarity=similarity,
                )
            )
        return hits

    # ------------------------------------------------------------------
    # 3. Pack a thread into a bucket
    # ------------------------------------------------------------------

    async def pack_topic(
        self,
        thread: ChatThread,
        *,
        bucket_id: uuid.UUID | None = None,
        bucket_slug: str | None = None,
        bucket_name: str | None = None,
    ) -> BucketSummary:
        """Summarise ``thread`` and attach to a bucket.

        One of ``bucket_id`` / ``bucket_slug`` / ``bucket_name`` must
        resolve a :class:`KnowledgeBucket` for the workspace. Slug /
        name auto-create the bucket when absent — the UI's "pack
        into new bucket" flow relies on this.

        Mutates ``thread.topic_summary`` and
        ``thread.packed_into_bucket_id`` so the chat UI can render the
        sentinel "Packed → <bucket>" entry without a follow-up DB
        read.
        """
        bucket = await self._resolve_or_create_bucket(
            bucket_id=bucket_id,
            bucket_slug=bucket_slug,
            bucket_name=bucket_name,
        )

        messages = await self._load_thread_messages(thread.id)
        if not messages:
            raise ValueError(
                f"thread {thread.id} has no messages to pack"
            )

        title, summary_text = await self._summarise_thread(messages)
        summary_embedding = await embed_text(
            f"{title}\n\n{summary_text}", settings=self._settings
        )

        summary_row = BucketSummary(
            bucket_id=bucket.id,
            thread_id=thread.id,
            title=title,
            summary=summary_text,
            embedding=summary_embedding,
            created_by_user_id=self._user_id,
        )
        self._session.add(summary_row)

        thread.topic_summary = _truncate(summary_text, 2000)
        thread.packed_into_bucket_id = bucket.id

        await self._session.flush()
        return summary_row

    async def _resolve_or_create_bucket(
        self,
        *,
        bucket_id: uuid.UUID | None,
        bucket_slug: str | None,
        bucket_name: str | None,
    ) -> KnowledgeBucket:
        if bucket_id is not None:
            row = (
                await self._session.execute(
                    select(KnowledgeBucket).where(
                        KnowledgeBucket.workspace_id == self._workspace_id,
                        KnowledgeBucket.id == bucket_id,
                    )
                )
            ).scalars().first()
            if row is None:
                raise ValueError(f"bucket {bucket_id} not found")
            return row

        if bucket_slug is None and bucket_name is None:
            raise ValueError(
                "pack_topic requires bucket_id, bucket_slug, or bucket_name"
            )

        slug = bucket_slug or _slugify(bucket_name or "bucket")
        row = (
            await self._session.execute(
                select(KnowledgeBucket).where(
                    KnowledgeBucket.workspace_id == self._workspace_id,
                    KnowledgeBucket.slug == slug,
                )
            )
        ).scalars().first()
        if row is not None:
            return row

        row = KnowledgeBucket(
            workspace_id=self._workspace_id,
            slug=slug,
            name=bucket_name or slug,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _load_thread_messages(
        self, thread_id: uuid.UUID
    ) -> list[ChatMessageRow]:
        return list(
            (
                await self._session.execute(
                    select(ChatMessageRow)
                    .where(ChatMessageRow.thread_id == thread_id)
                    .order_by(ChatMessageRow.created_at)
                )
            ).scalars().all()
        )

    async def _summarise_thread(
        self, messages: Sequence[ChatMessageRow]
    ) -> tuple[str, str]:
        """Ask the fast model for a ``(title, summary)`` pair."""
        transcript = "\n".join(
            f"[{m.role}] {m.body}" for m in messages if m.body
        )
        prompt = (
            "Summarise the following chat transcript so a future session "
            "can restart with the same context. Respond as JSON with two "
            "fields: `title` (≤ 80 chars) and `summary` (a few paragraphs, "
            "concrete and factual, no marketing). Transcript:\n\n"
            f"{transcript[:8000]}"
        )
        raw = await self._client.acomplete(
            [ChatMessage(role="user", content=prompt)],
            model=self._settings.agent_model_fast,
            max_tokens=1024,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = _salvage_json_object(raw)
        title = str(payload.get("title") or "Untitled topic")[:512]
        summary = str(payload.get("summary") or raw)[:8000]
        return title, summary

    # ------------------------------------------------------------------
    # 4. Assemble messages for the next LLM turn
    # ------------------------------------------------------------------

    async def assemble_messages(
        self,
        *,
        thread: ChatThread,
        recent_messages: Sequence[ChatMessageRow],
        new_user_message: str,
        retrieved_buckets: Sequence[BucketHit] = (),
        retrieved_kb: Sequence[KbChunk] = (),
    ) -> list[ChatMessage]:
        """Build the prompt the LLM sees for the next turn.

        Layering:

        1. System prompt.
        2. Optional "memory context" system message with the bucket
           summaries we retrieved. This is what makes the single-
           window experience work — the agent reads the warmed
           buckets as if they had always been in its system prompt.
        3. Optional "knowledge context" system message with
           ``.ship/knowledge`` snippets.
        4. The live thread history (bounded to ``_MAX_HISTORY_TURNS``
           so a long-running thread doesn't chew the context window).
        5. The new user message as the final turn.
        """
        out: list[ChatMessage] = [
            ChatMessage(role="system", content=_AGENT_SYSTEM_PROMPT),
        ]
        if thread.topic_summary:
            out.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Running topic summary (for continuity; do not quote "
                        f"verbatim unless asked):\n{thread.topic_summary}"
                    ),
                )
            )
        if retrieved_buckets:
            out.append(
                ChatMessage(
                    role="system",
                    content=_format_bucket_memory(retrieved_buckets),
                )
            )
        if retrieved_kb:
            out.append(
                ChatMessage(
                    role="system",
                    content=_format_kb_context(retrieved_kb),
                )
            )

        trimmed = _trim_history(recent_messages, _MAX_HISTORY_TURNS)
        for m in trimmed:
            if m.role not in {"user", "assistant"}:
                continue
            out.append(ChatMessage(role=m.role, content=m.body or ""))
        out.append(ChatMessage(role="user", content=new_user_message))
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Absolute cap on chat turns we send to the vendor. Past ~40 turns
# the prompt is both expensive and noisy; the thread's
# ``topic_summary`` should have absorbed the older content. 40 is
# a pragmatic cut — the pilot sees median thread length < 15.
_MAX_HISTORY_TURNS = 40


def _trim_history(
    messages: Sequence[ChatMessageRow], max_turns: int
) -> list[ChatMessageRow]:
    """Keep at most ``max_turns`` message rows, preserving role balance.

    We trim from the front — newest turns matter more than oldest
    ones, and the ``topic_summary`` system message is already
    carrying the early context.
    """
    if len(messages) <= max_turns:
        return list(messages)
    return list(messages[-max_turns:])


def _last_turns_text(
    messages: Sequence[ChatMessageRow], *, n_turns: int
) -> str:
    tail = list(messages[-n_turns:])
    return "\n".join(f"[{m.role}] {m.body}" for m in tail if m.body)


def _format_bucket_memory(hits: Sequence[BucketHit]) -> str:
    parts = ["Memory from related past conversations (warmed context):"]
    for h in hits:
        parts.append(
            f"- bucket `{h.bucket_slug}` / {h.title}\n  {_truncate(h.summary, 600)}"
        )
    return "\n".join(parts)


def _format_kb_context(chunks: Sequence[KbChunk]) -> str:
    parts = ["Relevant excerpts from `.ship/knowledge`:"]
    for c in chunks:
        parts.append(
            f"- {c.source_path} (chunk {c.chunk_index})\n  "
            f"{_truncate(c.content, 400)}"
        )
    return "\n".join(parts)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _salvage_json_object(raw: str) -> dict[str, object]:
    """Best-effort JSON extraction for models that add prose around JSON."""
    if not raw:
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _slugify(value: str) -> str:
    """Minimal slugifier for auto-generated bucket slugs."""
    out = []
    for ch in value.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_"}:
            out.append("-")
    slug = "".join(out).strip("-") or "bucket"
    return slug[:120]


__all__ = ["BucketHit", "TopicService", "TopicShiftDecision"]
