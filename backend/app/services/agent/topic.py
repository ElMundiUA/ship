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
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketSource,
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
from backend.app.services.agent.embedding import embed_text, embed_texts
from backend.app.services.bucket_visibility import visible_to_user_clause
from backend.app.services.bucket_summary_articles import (
    mirror_summary_to_article,
)
from backend.app.services import agent_roles as agent_roles_svc
from backend.app.services.policies import render_policies_preamble


logger = logging.getLogger(__name__)


# The Navigator system prompt now lives in the catalog at
# ``artifacts/patterns/role-navigator/ARTIFACT.md`` so prompt edits
# and policy injection ride one update path. ``_load_navigator_prompt``
# pulls the artifact body at call time (catalog is mtime-cached). The
# string literal below is a defensive fallback for the rare case the
# catalog miss happens — keeping the chat surface up even if the
# artifact file is misplaced beats serving a 500.
_NAVIGATOR_ROLE_SLUG = "navigator"


def _load_navigator_prompt() -> str:
    """Return the Navigator system prompt body from the agent-role registry.

    Falls back to ``_NAVIGATOR_FALLBACK_PROMPT`` when the ``navigator``
    Ship default is missing — defensive only; the file ships under
    ``backend/app/resources/agent_roles/navigator.md``, so a miss
    means a deployment bug, not an expected branch.
    """
    default = agent_roles_svc.get_default(_NAVIGATOR_ROLE_SLUG)
    if default is None or not default.prompt.strip():
        return _NAVIGATOR_FALLBACK_PROMPT
    return default.prompt.strip()


_PROJECT_DRAFTING_MODE_PROMPT = (
    "## Mode: Project drafting\n"
    "\n"
    "This thread was opened from the dashboard's **+ New project** "
    "CTA. The user is here to shape ONE feature vision into a "
    "Linear project (or Jira epic). Run the conversation like this:\n"
    "\n"
    "1. **Reflect** the user's idea back in 3 short bullets — who it's "
    "for, why it matters, what's explicitly out of scope. Don't pad.\n"
    "2. **Ask the single sharpest open question** that closes the "
    "biggest ambiguity. ONE question per turn — not a checklist.\n"
    "3. As the brief firms up, **draft the project body inline as a "
    "preview** (a markdown block titled `## Project draft`). Sections: "
    "**Goal**, **Scope**, **Non-goals**, **Open questions**. Edit the "
    "preview across turns; don't rewrite the whole thing each time.\n"
    "4. **Do NOT call `project_create` until the user explicitly "
    "confirms** ('save it', 'looks good', 'ship it', equivalent). "
    "Premature commit is the failure mode this mode exists to "
    "prevent — the project ends up on the dashboard before the brief "
    "is ready, then sits there as noise.\n"
    "5. After `project_create` lands, **prefer "
    "`project_update body_append=...`** over chatter for any further "
    "body edits. NEVER spawn a second `project_create` for the same "
    "idea — that produces ghost projects on the dashboard.\n"
    "\n"
    "The dashboard places the new project in the **Drafts** bucket "
    "(internal enum value `priority_state='planning'`). The agent's "
    "autonomous picker does not consume Drafts — the user has to "
    "explicitly hand the project off to decomposition before any "
    "specialist runs. You don't need to tell the user this; just "
    "don't promise immediate work."
)

_NAVIGATOR_FALLBACK_PROMPT = (
    # Defensive only — fired when ``agent_roles_svc.get_default("navigator")``
    # returns nothing, which means a deployment bug (the file at
    # ``backend/app/resources/agent_roles/navigator.md`` is the canonical
    # source). Keep it short so it never accidentally drifts out of sync
    # with the live role-file; the in-production prompt is loaded from
    # disk every call.
    "You are Ship Navigator, a software-engineering agent in a single "
    "chat window. The full playbook lives in your workspace's "
    "``navigator`` agent role file; this defensive stub means that "
    "file is missing. Operate concretely + cite tool evidence; ask "
    "the operator to redeploy if you keep hitting this."
)


@dataclass(slots=True)
class TopicShiftDecision:
    """Structured output from :meth:`TopicService.classify_shift`.

    ``shifted`` is a boolean the UI gates the banner on; ``reason``
    is a short human-readable string we show as a tooltip so the
    user can see *why* the agent thinks they're on a new topic,
    and ``new_title`` is a proposed one-line label for the new
    conversation thread.

    ``explicit_phrase`` is True when the user said something like
    "let's switch" / "теперь другое" — a high-confidence signal that
    short-circuits the LLM classifier. The UI uses it to elevate the
    banner copy from a soft "looks like you moved on" into a direct
    "switching now" pill the user can undo.
    """

    shifted: bool
    reason: str
    new_title: str | None
    explicit_phrase: bool = False


# Canonical RU + EN phrases that mean "I'm changing the topic". Keep
# the list short and high-precision — every entry is a regex anchored
# at a word boundary so we don't false-trigger on substrings ("switch
# to dark mode" must not match "switch"). When the user says these,
# we skip the LLM classifier entirely and surface the shift banner
# with ``explicit_phrase=True`` so the UI can act decisively.
_EXPLICIT_SHIFT_PHRASES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # English
        r"\b(let'?s|let us)\s+(switch|move on|change topics?|talk about something else)\b",
        r"\bnew topic\b",
        r"\bchange (the\s+)?(topic|subject)\b",
        r"\bdifferent (topic|subject|question)\b",
        r"\bswitch(ing)?\s+(topics?|gears|subjects?)\b",
        r"\bforget (about )?(this|that|it|all that)\b",
        r"\bnever mind( that)?\b",
        r"\bmoving on\b",
        # Russian
        r"\b(давай|давайте)\s+(переключимся|сменим тему|о другом|про другое)\b",
        r"\bтеперь (другое|другая тема|про другое|совсем другое)\b",
        r"\bновая тема\b",
        r"\bсменим тему\b",
        r"\b(забудь|забей)( это| про это| об этом)?\b",
        r"\bпереключимся\b",
        r"\bдругой вопрос\b",
    )
)


def detect_explicit_shift(message: str) -> bool:
    """Return True when ``message`` matches a canonical "I'm switching
    topics" phrase. Cheap regex pre-filter — runs before the LLM
    classifier so the obvious cases short-circuit.
    """
    if not message:
        return False
    return any(p.search(message) for p in _EXPLICIT_SHIFT_PHRASES)


# ELS-59 — cosine-distance soft-switch.
#
# Between the explicit-phrase regex and the LLM classifier we run a
# cheap embedding compare: prior conversation context vs new user
# message. The intuition is that a clear topic shift produces an
# embedding far from the running thread, while continuations cluster
# tight. Both extremes short-circuit the LLM (saves ~200ms per turn);
# the middle band falls through to the LLM where intent + tool-domain
# signal can break the tie.
#
# Thresholds are tuned for ``text-embedding-3-small``. Distances on
# short chat messages tend to land in 0.20–0.55 for related text and
# > 0.55 for genuinely different topics. We pin the shift gate at
# 0.60 (matches the spec in the ELS-59 ticket) and the continue gate
# at 0.30 — anything below that is "very obviously the same topic".
_COSINE_SHIFT_THRESHOLD: Final[float] = 0.60
_COSINE_CONTINUE_THRESHOLD: Final[float] = 0.30
# Below this, the prior context is too thin to compare against (e.g.
# the very first turn was a one-word "hi"); we'd just be embedding
# noise. Bail to the LLM instead — it has the running summary plus
# raw turns to work with.
_COSINE_MIN_PRIOR_CHARS: Final[int] = 60


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Distance in the embedding space; 0 = identical, 1 = orthogonal,
    2 = opposite. Numpy would be faster but it's a dim=1536 dot
    product, ~5µs in pure Python — not worth the import.
    """
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    sim = dot / ((norm_a**0.5) * (norm_b**0.5))
    if sim > 1.0:
        sim = 1.0
    elif sim < -1.0:
        sim = -1.0
    return 1.0 - sim


def _build_prior_cosine_context(
    running_summary: str | None,
    user_turns: Sequence[ChatMessageRow],
) -> str:
    """Glue the running summary + last few user turns into a single
    string for cosine compare. We include the summary first when it
    exists (it's denser per-character than raw turns), then the last
    three user messages — enough recency to capture a topic without
    diluting the embedding with old chatter.
    """
    parts: list[str] = []
    if running_summary and running_summary.strip():
        parts.append(running_summary.strip())
    for turn in user_turns[-3:]:
        body = (turn.body or "").strip()
        if body:
            parts.append(body)
    return "\n".join(parts)


@dataclass(slots=True)
class BucketHit:
    """One bucket-article match returned by :meth:`retrieve_buckets`.

    Phase 5c cutover: the underlying row is a :class:`BucketArticle`
    now (mirrored from the old :class:`BucketSummary` via Phase 5b),
    so the field is ``article_id``. The body is the article's
    ``body_md`` — for an ``agent_memory`` article that's exactly the
    same text the old ``BucketSummary.summary`` held, so the downstream
    prompt-assembly layer doesn't need to change shape. ``summary``
    stays as the attribute name because it's what :func:`_format_bucket_memory`
    expects; it's the "packed conversation" text regardless of source.
    """

    bucket_id: uuid.UUID
    bucket_slug: str
    bucket_name: str
    article_id: uuid.UUID
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

        # Cheap regex pre-filter: explicit "let's switch" / "теперь
        # другое" short-circuits the LLM classifier. Trades a tiny bit
        # of latency for a more decisive UX on the obvious cases.
        if detect_explicit_shift(new_user_message):
            return TopicShiftDecision(
                shifted=True,
                reason="You said you're switching topics.",
                new_title=None,
                explicit_phrase=True,
            )

        # ELS-59 — cosine-distance soft-switch. One embedding batch
        # (prior context + new message) decides clear continuations
        # and clear shifts without the LLM round-trip. The middle
        # band falls through to the LLM below where running_summary
        # + raw turns give it more to chew on.
        prior_text = _build_prior_cosine_context(
            running_summary, prior_user_turns
        )
        if (
            prior_text
            and len(prior_text) >= _COSINE_MIN_PRIOR_CHARS
            and new_user_message.strip()
        ):
            try:
                vectors = await embed_texts(
                    [prior_text, new_user_message],
                    settings=self._settings,
                )
            except Exception as exc:  # noqa: BLE001
                # Missing OPENAI_API_KEY raises here; rate limits and
                # transient network errors too. None of those should
                # block the turn — fall through to the LLM classifier
                # which is the pre-cosine status quo.
                logger.warning(
                    "topic-shift cosine pre-filter failed: %s", exc
                )
            else:
                if len(vectors) == 2:
                    distance = _cosine_distance(vectors[0], vectors[1])
                    logger.debug(
                        "topic-shift cosine distance=%.3f (shift=%.2f / "
                        "continue=%.2f)",
                        distance,
                        _COSINE_SHIFT_THRESHOLD,
                        _COSINE_CONTINUE_THRESHOLD,
                    )
                    if distance >= _COSINE_SHIFT_THRESHOLD:
                        return TopicShiftDecision(
                            shifted=True,
                            reason=(
                                "This question reads like a different "
                                "topic from what we've been on."
                            ),
                            new_title=None,
                            explicit_phrase=False,
                        )
                    if distance <= _COSINE_CONTINUE_THRESHOLD:
                        return TopicShiftDecision(
                            shifted=False, reason="", new_title=None
                        )
                # else: borderline — fall through to LLM.

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
        """Top-K packed-topic articles semantically close to ``query``.

        Phase 5c cutover: reads from :class:`BucketArticle` (mirrored
        from :class:`BucketSummary` in Phase 5b) instead of
        :class:`BucketSummary` directly. The behavioural scope is
        unchanged — only ``agent_memory`` buckets contribute here,
        because ``repo_files`` content is already surfaced by the
        dedicated kb_indexer/RAG path, not by the memory retriever.

        Returns entries whose cosine-similarity clears
        ``similarity_threshold`` — this keeps "no useful memory"
        from polluting the prompt with unrelated packs. The threshold
        is deliberately permissive (0.25) because
        text-embedding-3-small's cosine distance distribution is
        narrow; callers can tighten via the argument.
        """
        qvec = await embed_text(query, settings=self._settings)
        stmt = (
            select(
                BucketArticle,
                KnowledgeBucket.slug,
                KnowledgeBucket.name,
                BucketArticle.embedding.cosine_distance(qvec).label("dist"),
            )
            .join(
                KnowledgeBucket,
                KnowledgeBucket.id == BucketArticle.bucket_id,
            )
            .where(KnowledgeBucket.workspace_id == self._workspace_id)
            .where(KnowledgeBucket.archived_at.is_(None))
            # Only published articles contribute — drafts and the
            # superseded history from Phase 5a's repo_files versioning
            # would otherwise re-surface stale content.
            .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
            .where(BucketArticle.archived_at.is_(None))
            # Without an embedding we can't rank — and agent-memory
            # articles always carry one over from the summary row
            # (Phase 5b mirror). Filtering NULL here covers the repo_files
            # case where Phase 5a hasn't run the embedder yet, and would
            # otherwise throw at cosine_distance time.
            .where(BucketArticle.embedding.isnot(None))
            # Retrieval scope: preserve v1 semantics — only memory from
            # packed conversations feeds the prompt's "warmed context".
            .where(KnowledgeBucket.source_kind == BucketSource.AGENT_MEMORY)
            # Phase 8: never surface another user's ``scope=user``
            # memory. The helper keeps this in sync with Phase 3's
            # resolver — any tightening there lands here for free.
            .where(visible_to_user_clause(self._user_id))
            .order_by("dist")
            .limit(max(1, min(limit, 10)))
        )
        rows = (await self._session.execute(stmt)).all()
        hits: list[BucketHit] = []
        for article, slug, name, dist in rows:
            similarity = 1.0 - float(dist)
            if similarity < similarity_threshold:
                continue
            hits.append(
                BucketHit(
                    bucket_id=article.bucket_id,
                    bucket_slug=slug,
                    bucket_name=name,
                    article_id=article.id,
                    title=article.title,
                    summary=article.body_md,
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
        # Client-side id so the Phase 5b article mirror can reference
        # it in the same flush without a mid-transaction round-trip.
        # server_default=gen_random_uuid() still wins whenever the row
        # is inserted via another path that doesn't go through here.
        summary_row.id = uuid.uuid4()
        self._session.add(summary_row)

        thread.topic_summary = _truncate(summary_text, 2000)
        thread.packed_into_bucket_id = bucket.id

        # Phase 5b: dual-write into ``bucket_articles`` so every new
        # packed summary immediately has an article mirror. The mirror
        # is the long-term read surface (Phase 5c); until then it's a
        # passive shadow, but it's cheaper to keep it in sync on the
        # happy path than to run a catch-up backfill later. The mirror
        # is best-effort — a failure here must not block the pack, so
        # we log and move on.
        try:
            await mirror_summary_to_article(self._session, summary_row)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "pack_topic: article mirror failed for summary %s: %s",
                summary_row.id,
                exc,
            )

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
        new_user_attachments: "Sequence[Any]" = (),
        retrieved_buckets: Sequence[BucketHit] = (),
        retrieved_kb: Sequence[KbChunk] = (),
    ) -> list[ChatMessage]:
        """Build the prompt the LLM sees for the next turn.

        Layering:

        1. System prompt.
        2. Workspace policies preamble (Workspace policy injection):
           prose standing rules ("Always work via PR", "Never commit
           secrets") rendered just after the system prompt so the
           model treats them as hard constraints, not soft hints.
           Skipped when the workspace has no enabled policies.
        3. Optional "memory context" system message with the bucket
           summaries we retrieved. This is what makes the single-
           window experience work — the agent reads the warmed
           buckets as if they had always been in its system prompt.
        4. Optional "knowledge context" system message with
           ``.ship/knowledge`` snippets.
        5. The live thread history (bounded to ``_MAX_HISTORY_TURNS``
           so a long-running thread doesn't chew the context window).
        6. The new user message as the final turn.
        """
        # Collect dynamic identity / surface facts BEFORE rendering the
        # session-context frame so the agent reads workspace name +
        # bound tracker + activated repos + inbox snapshot at the top
        # of every turn. Best-effort: if collection itself raises, fall
        # back to the bare frame so a transient DB hiccup doesn't sink
        # the chat — this is observability, not correctness.
        try:
            facts = await _collect_session_facts(
                self._session,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                settings=self._settings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session-facts: collection failed workspace=%s err=%s",
                self._workspace_id,
                exc,
            )
            facts = None
        out: list[ChatMessage] = [
            ChatMessage(role="system", content=_load_navigator_prompt()),
            ChatMessage(role="system", content=_render_session_context(
                workspace_id=self._workspace_id,
                now=datetime.now(timezone.utc),
                facts=facts,
            )),
        ]
        # Policies preamble lives between the static system prompt and
        # the dynamic per-thread context (topic summary / buckets / kb)
        # so that workspace-level invariants take precedence over the
        # warmed memory the agent might otherwise lean on. One DB read
        # per assemble — workspaces have a handful of policies at most,
        # so this is cheap enough not to need a memo cache. Navigator
        # runs as its own specialist (``role-navigator``) so the
        # role-scoped policies seeded for it (no-fabrication tooling
        # rules, session-time-from-context, …) join the global ones.
        policies_preamble = await render_policies_preamble(
            self._session,
            self._workspace_id,
            role_slug=_NAVIGATOR_ROLE_SLUG,
        )
        if policies_preamble:
            out.append(
                ChatMessage(role="system", content=policies_preamble)
            )
        # ELS-74: drafting mode. When the thread carries
        # ``intent='shape_project'`` the dashboard's "+ New project" CTA
        # opened it; bias Navigator toward shaping a brief and waiting
        # for explicit confirmation before calling ``create_project``.
        # The block lives here (after policies, before per-thread
        # warmed memory) so workspace-level rules still take precedence
        # but the mode override beats the topic summary, which can
        # drift across turns.
        if thread.intent == "shape_project":
            out.append(
                ChatMessage(
                    role="system",
                    content=_PROJECT_DRAFTING_MODE_PROMPT,
                )
            )
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
        out.append(
            ChatMessage(
                role="user",
                content=new_user_message,
                attachments=list(new_user_attachments),
            )
        )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Absolute cap on chat turns we send to the vendor. Past ~40 turns
# the prompt is both expensive and noisy; the thread's
# ``topic_summary`` should have absorbed the older content. 40 is
# a pragmatic cut — the pilot sees median thread length < 15.
_MAX_HISTORY_TURNS = 40


@dataclass(frozen=True)
class _RepoSnapshot:
    """One activated repo, projected into the session context.

    Replaces the three retired discovery tools — ``list_activated_repos``,
    ``repo_intel_get``, ``repo_kb_status`` — by carrying their payloads
    inline so the LLM never has to round-trip for the basics. Anything
    that needs structured / pageable data (full intel JSONB, per-chunk
    KB inspection) is still a tool call away.
    """

    id: str  # repo UUID, what tools accept as ``repo_id``
    full_name: str
    default_branch: str
    top_languages: list[str]  # up to 3, ordered by share
    frameworks: list[str]     # up to 4, ordered as harvested
    kb_chunk_count: int
    kb_last_indexed_at: datetime | None


@dataclass(frozen=True)
class _SessionFacts:
    """Pre-fetched context the renderer formats into the system frame.

    Decoupled from the async fetch so ``_render_session_context`` stays
    a pure function — the test surface is then "given these facts,
    does the markdown look right?", not "given these mocks, do these
    DB calls fire in this order?".
    """

    workspace_name: str | None
    workspace_slug: str | None
    user_name: str | None
    user_email: str | None
    tracker_kind: str | None  # "linear" / "jira" / "github_issues" / etc; None if unbound
    tracker_scope_hint: str | None  # Linear team key, Jira project key, etc.
    tracker_status: str  # "connected" / "error" / "disconnected"
    tracker_health_error: str | None
    repos: list[_RepoSnapshot]  # activated repos with intel + KB freshness
    inbox_open_total: int
    inbox_by_type: dict[str, int]


_INBOX_OPEN_STATES: tuple[str, ...] = ("new", "snoozed")
_REPOS_PREVIEW_CAP = 6  # ``+N more`` past this — frame stays under ~250 tokens
_LANG_PREVIEW_CAP = 3   # top languages by share — keep the line compact
_FW_PREVIEW_CAP = 4     # frameworks — same logic


async def _collect_session_facts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    settings: Settings,
) -> _SessionFacts:
    """Fetch the dynamic facts the agent should see at the top of every turn.

    Two-three lightweight DB hits + one tracker resolve. We trade a
    handful of milliseconds per turn for the agent NOT having to dial
    ``list_integrations`` / ``list_activated_repos`` / ``inbox_counts``
    just to know where it lives. Anything that fails (tracker probe
    raised, inbox query timed out) degrades to a polite null in the
    rendered frame — never blocks the LLM call.
    """
    from sqlalchemy import select as _select  # local: avoid topping the file

    from backend.app.db.models.tenancy import User, Workspace
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.agent_memory import KbChunk
    from backend.app.db.models.repo_intel import RepoIntel
    from backend.app.services.tracker_resolver import resolve_for_workspace

    workspace = await session.get(Workspace, workspace_id)
    user = await session.get(User, user_id)

    # Pull every activated repo + its current intel snapshot + KB
    # freshness aggregates in one go. Each scalar is null-safe so a
    # never-harvested or never-indexed repo still surfaces with a
    # graceful "intel: pending" / "KB: never indexed" line — the
    # LLM should NEVER see this query throw.
    repo_rows = (
        await session.execute(
            _select(
                WorkspaceRepo.id,
                WorkspaceRepo.full_name,
                WorkspaceRepo.default_branch,
                RepoIntel.languages,
                RepoIntel.frameworks,
                func.count(KbChunk.id).label("kb_count"),
                func.max(KbChunk.indexed_at).label("kb_last"),
            )
            .select_from(WorkspaceRepo)
            .join(
                RepoIntel,
                (RepoIntel.repo_id == WorkspaceRepo.id)
                & (RepoIntel.is_current.is_(True)),
                isouter=True,
            )
            .join(
                KbChunk,
                KbChunk.repo_id == WorkspaceRepo.id,
                isouter=True,
            )
            .where(WorkspaceRepo.workspace_id == workspace_id)
            .group_by(
                WorkspaceRepo.id,
                WorkspaceRepo.full_name,
                WorkspaceRepo.default_branch,
                RepoIntel.languages,
                RepoIntel.frameworks,
            )
            .order_by(WorkspaceRepo.full_name)
        )
    ).all()
    repos: list[_RepoSnapshot] = []
    for row in repo_rows:
        # ``languages`` JSONB is ``{"python": 0.62, "typescript": 0.31, …}``;
        # take the top N by share to keep the frame compact.
        lang_map: dict[str, float] = dict(row.languages or {})
        top_langs = [
            k for k, _ in sorted(lang_map.items(), key=lambda kv: -kv[1])
        ][:_LANG_PREVIEW_CAP]
        fw_list = list(row.frameworks or [])[:_FW_PREVIEW_CAP]
        repos.append(
            _RepoSnapshot(
                id=str(row.id),
                full_name=str(row.full_name),
                default_branch=str(row.default_branch),
                top_languages=top_langs,
                frameworks=fw_list,
                kb_chunk_count=int(row.kb_count or 0),
                kb_last_indexed_at=row.kb_last,
            )
        )

    inbox_total = (
        await session.execute(
            _select(func.count(InboxItem.id)).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status.in_(_INBOX_OPEN_STATES),
            )
        )
    ).scalar_one() or 0

    inbox_by_type_rows = (
        await session.execute(
            _select(InboxItem.type, func.count(InboxItem.id))
            .where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status.in_(_INBOX_OPEN_STATES),
            )
            .group_by(InboxItem.type)
        )
    ).all()
    inbox_by_type: dict[str, int] = {
        str(row[0]): int(row[1]) for row in inbox_by_type_rows if row[0]
    }

    tracker_kind: str | None = None
    tracker_scope_hint: str | None = None
    tracker_status = "disconnected"
    tracker_health_error: str | None = None
    try:
        resolved = await resolve_for_workspace(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 — defensive, don't block the turn
        logger.warning(
            "session-facts: tracker resolve failed workspace=%s err=%s",
            workspace_id,
            exc,
        )
        resolved = None
        tracker_status = "error"
        tracker_health_error = str(exc)
    if resolved is not None:
        tracker_kind = resolved.kind
        tracker_scope_hint = resolved.scope_hint
        tracker_status = "error" if resolved.last_health_error else "connected"
        tracker_health_error = resolved.last_health_error

    return _SessionFacts(
        workspace_name=workspace.name if workspace else None,
        workspace_slug=workspace.slug if workspace else None,
        user_name=user.display_name if user else None,
        user_email=user.email if user else None,
        tracker_kind=tracker_kind,
        tracker_scope_hint=tracker_scope_hint,
        tracker_status=tracker_status,
        tracker_health_error=tracker_health_error,
        repos=repos,
        inbox_open_total=int(inbox_total),
        inbox_by_type=inbox_by_type,
    )


def _render_session_context(
    *, workspace_id: uuid.UUID, now: datetime, facts: _SessionFacts | None = None
) -> str:
    """Dynamic frame the agent reads right after the static prompt.

    Pins today's date, the active workspace + user identity, the
    bound tracker, the activated repo set, and the open inbox counts
    so the model can't fall back on training-data assumptions ("it
    must be 2024 / 2025") and doesn't have to dial three discovery
    tools every turn just to learn where it lives.

    Keep the frame under ~250 tokens — every turn's prefix counts.
    Repo list is capped at ``_REPOS_PREVIEW_CAP`` with a "+N more"
    tail; the agent calls ``list_activated_repos`` if it needs the
    rest. Tracker line is single-line per-state so a degraded
    workspace still surfaces the failure mode (``status=error`` +
    last health error) rather than vanishing into a polite default.

    The static system prompt references the **Session context**
    heading by name in its "Today's date" rule and in the
    don't-re-ask-context rule — if you rename the heading, update
    the prompt too.
    """
    iso_date = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")
    lines: list[str] = [
        "## Session context",
        "",
        f"- Today: **{iso_date} ({weekday}) UTC**.",
    ]
    if facts is None:
        lines.append(f"- Active workspace id: `{workspace_id}`.")
    else:
        if facts.workspace_name and facts.workspace_slug:
            lines.append(
                f"- Workspace: **{facts.workspace_name}** "
                f"(`{facts.workspace_slug}`, id `{workspace_id}`)."
            )
        else:
            lines.append(f"- Active workspace id: `{workspace_id}`.")
        if facts.user_name and facts.user_email:
            lines.append(
                f"- You're talking to: **{facts.user_name}** <{facts.user_email}>."
            )
        elif facts.user_email:
            lines.append(f"- You're talking to: <{facts.user_email}>.")
        if facts.tracker_kind:
            scope = (
                f" scope `{facts.tracker_scope_hint}`"
                if facts.tracker_scope_hint
                else ""
            )
            tracker_line = (
                f"- Bound tracker: **{facts.tracker_kind}**"
                f"{scope} · status={facts.tracker_status}"
            )
            if facts.tracker_status == "error" and facts.tracker_health_error:
                tracker_line += f" ({facts.tracker_health_error[:120]})"
            lines.append(tracker_line + ".")
        else:
            lines.append("- Bound tracker: **none** — `ticket_create` / `ticket_list` will refuse until one is bound.")
        if facts.repos:
            preview = facts.repos[:_REPOS_PREVIEW_CAP]
            lines.append(f"- Activated repos ({len(facts.repos)}):")
            for r in preview:
                fragments: list[str] = [r.default_branch]
                if r.top_languages:
                    fragments.append("/".join(r.top_languages))
                if r.frameworks:
                    fragments.append("/".join(r.frameworks))
                # KB freshness — last-indexed timestamp tells the LLM
                # whether ``knowledge_search`` is worth trusting before
                # falling back to ``repo_file_get``. ``never indexed``
                # is more useful than a missing line.
                if r.kb_chunk_count == 0:
                    fragments.append("KB: never indexed")
                else:
                    when = (
                        r.kb_last_indexed_at.strftime("%Y-%m-%d")
                        if r.kb_last_indexed_at
                        else "?"
                    )
                    fragments.append(
                        f"KB: {r.kb_chunk_count} chunks, indexed {when}"
                    )
                lines.append(
                    f"  - `{r.full_name}` (id `{r.id}`) — "
                    + " · ".join(fragments)
                )
            if len(facts.repos) > _REPOS_PREVIEW_CAP:
                lines.append(
                    f"  - … + {len(facts.repos) - _REPOS_PREVIEW_CAP} more (omitted for brevity)"
                )
        else:
            lines.append("- Activated repos: **none** — point the operator at `/onboarding` if they want to ship code.")
        if facts.inbox_open_total > 0:
            by_type = ", ".join(
                f"{k}={v}" for k, v in sorted(facts.inbox_by_type.items())
            )
            lines.append(
                f"- Inbox: **{facts.inbox_open_total} open**"
                + (f" ({by_type})" if by_type else "")
                + "."
            )
        else:
            lines.append("- Inbox: **0 open**.")
    lines.append(
        "- Use these for \"today\" / \"yesterday\" / \"this week\" "
        "wording AND for routing decisions. Never re-ask the user for "
        "anything already on this list; never assume year/month from "
        "your training data."
    )
    return "\n".join(lines)


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
