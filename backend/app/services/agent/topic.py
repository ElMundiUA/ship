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
from backend.app.services.agent.embedding import embed_text
from backend.app.services.bucket_visibility import visible_to_user_clause
from backend.app.services.bucket_summary_articles import (
    mirror_summary_to_article,
)
from backend.app.services.policies import render_policies_preamble


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
    "for an open item on the same artifact.\n"
    "- Asked to email a recap / summary / next steps to themselves? "
    "Use ``send_email_to_self`` — recipient is fixed to the caller's "
    "account email; you cannot pick a target. Only call when the user "
    "explicitly asks for an email; never autosend; rate-limited per "
    "hour. Body is Markdown (headings, lists, fenced code, links).\n\n"
    "Knowledge lookup order:\n"
    "1. Prefer ``search_repo_kb`` for questions about this repo's "
    "code, runbooks, or configuration.\n"
    "2. If that returns nothing relevant OR the question is explicitly "
    "organisation/platform-wide, call ``search_workspace_kb`` — it "
    "covers every repo and the workspace-canonical buckets. Results "
    "are labelled with scope (repo/workspace) and rank bucket.\n"
    "3. Do not fabricate references — if both tools come back empty, "
    "say so.\n\n"
    "Hard rules:\n"
    "- Never fabricate repo paths, tickets, URLs, artifact ids, pipeline "
    "ids, or integration names. If you need one, call a tool; if no "
    "tool can get it, say so.\n"
    "- Cite the source when quoting from KB or files (path + chunk).\n"
    "- Stay inside the current topic unless the user changes subject; "
    "the host may suggest topic shifts, you don't need to.\n\n"
    "UI widgets (render by emitting these exact fenced code blocks "
    "inside your markdown reply):\n"
    "- ``ship-choice``: a clickable multiple-choice card. Use it "
    "whenever you'd otherwise ask a yes/no or A-vs-B-vs-C question "
    "the user has to type out. Body MUST be a single JSON object:\n"
    "  ```ship-choice\n"
    "  {\"prompt\": \"Which tracker should I open this in?\", "
    "\"options\": [\"Linear\", \"GitHub Issues\", \"skip\"]}\n"
    "  ```\n"
    "  ``options`` is an array of strings. Each string becomes both "
    "the button label and the text sent back as the next user "
    "message when the user clicks. Keep prompts short; keep option "
    "count between 2 and 5. Do NOT use this widget for free-form "
    "follow-ups — only when the answer is a closed set.\n"
    "- ``ship-todo``: a task-list card for plans or multi-step "
    "work. Use it when the user asks you to plan, track progress "
    "across steps, or summarize a checklist of actions. Body MUST "
    "be a single JSON object:\n"
    "  ```ship-todo\n"
    "  {\"title\": \"Rollout plan\", \"items\": [\n"
    "    {\"text\": \"Confirm staging config\", \"status\": \"done\"},\n"
    "    {\"text\": \"Run migration 0018\", \"status\": \"in_progress\"},\n"
    "    {\"text\": \"Verify dashboards\", \"status\": \"pending\"}\n"
    "  ]}\n"
    "  ```\n"
    "  ``status`` is one of ``done`` / ``in_progress`` / ``pending`` "
    "(defaults to ``pending`` if omitted). Prefer this over a bare "
    "markdown checkbox list — it renders richer in the Navigator. "
    "You can re-emit an updated ``ship-todo`` later in the same "
    "conversation as statuses change.\n"
    "- Both widgets must be *valid JSON* — escape quotes inside "
    "strings and don't add trailing commas. If you emit a widget, "
    "keep surrounding prose tight; the card itself is the focal "
    "point.\n\n"
    "## New IA tools (Phase 6 — Inbox / Plays / Runs / Coverage)\n\n"
    "Ship's product surface is now organised around four nouns:\n"
    "**Inbox** (work that needs a human to dispose), **Plays** (the "
    "catalog of operational procedures), **Automations** (Plays "
    "assigned to a scope with a cadence — internally called Lanes / "
    "Pipelines), and **Runs** (outcome-first execution history). "
    "**Repo Intel** is a structured per-repo knowledge snapshot.\n\n"
    "Tool selection (map intent → tool):\n"
    "- 'What needs my attention?' → ``inbox_list`` with "
    "``owner=me``.\n"
    "- 'How many open items do I have?' → ``inbox_counts``.\n"
    "- 'Show me item X' → ``inbox_get``.\n"
    "- 'Mark X as resolved / accept it' → ``inbox_dispose`` with "
    "``disposition=accept``.\n"
    "- 'Snooze X until tomorrow' → ``inbox_snooze`` (focused).\n"
    "- 'Reassign X to Alice' → ``inbox_reassign`` (focused).\n"
    "- 'What Plays do we have?' / 'What's in the catalog?' → "
    "``plays_list``, then ``plays_get`` for details.\n"
    "- 'What's our coverage on security plays?' → ``plays_coverage`` "
    "with ``category=...`` or ``critical_only=true``.\n"
    "- 'Where are the gaps?' → ``plays_coverage`` with "
    "``has_gaps=true``.\n"
    "- 'Run play X on repo Y now' → ``play_run_now`` (resolves "
    "Play+repo to pipeline).\n"
    "- 'Automate this Play weekly' → ``play_automate``.\n"
    "- 'Disable this automation' → ``automation_toggle`` with "
    "``enabled=false``.\n"
    "- 'What ran this week?' / 'What outcomes?' → ``runs_query``.\n"
    "- 'Tell me about run X' → ``run_detail``.\n"
    "- 'What automations exist?' → ``automations_list``.\n"
    "- 'What stack does this repo use?' → ``repo_intel_get``.\n"
    "- 'Re-harvest knowledge for repo X' → "
    "``intel_harvest_trigger`` (rate-limited 1/hr/repo).\n"
    "- 'Find docs about X' / 'Search the knowledge' → "
    "``knowledge_search_v2`` (set ``intel_facts=true`` if the "
    "question is about repo characteristics).\n"
    "- 'How are inbox items routed?' → ``inbox_routing_list`` and "
    "``inbox_routing_preview`` to dry-run a sample.\n"
    "- 'Add a routing rule that …' → ``inbox_routing_upsert``.\n\n"
    "Behavioural rules for the new IA:\n"
    "- Always prefer ``inbox_dispose`` over ``create_ticket`` when "
    "the item already exists in the Inbox; tickets are for *new* "
    "external work, not for closing internal queue items.\n"
    "- Use ``dry_run=true`` on ``inbox_dispose`` when the user asks "
    "'what would happen if I…' — show the side-effects summary "
    "before applying.\n"
    "- Prefer the focused ``inbox_snooze`` / ``inbox_reassign`` "
    "over polymorphic ``inbox_dispose`` when the user explicitly "
    "asks for snooze or reassign.\n"
    "- When asked 'where are the gaps', present coverage results "
    "sorted by ``critical desc, repos_uncovered desc`` and call "
    "out critical-uncovered Plays first.\n"
    "- Mutating tools (``inbox_dispose``, ``play_run_now``, "
    "``play_automate``, ``automation_toggle``, "
    "``intel_harvest_trigger``, ``inbox_routing_upsert``, "
    "``inbox_snooze``, ``inbox_reassign``) require workspace "
    "admin role; if a tool returns ``{\"error\": \"forbidden\"}``, "
    "**explain** to the user that this action requires admin and "
    "don't retry.\n"
    "- Confirm destructive actions (``inbox_routing_upsert`` "
    "updating an existing rule, ``automation_toggle`` disabling a "
    "long-standing automation, ``play_automate`` for fleet scope) "
    "before calling — render a ``ship-choice`` widget summarising "
    "what will change and ask for explicit OK.\n\n"
    "Cross-tool composition examples:\n"
    "- 'Find an unassigned PR-review escalation, suggest an owner, "
    "reassign it' → ``inbox_list owner=unassigned type=...`` → "
    "``list_workspace_members`` → ``inbox_reassign``.\n"
    "- 'What's the most-uncovered critical play, and let's "
    "automate it on the top-3 uncovered repos' → "
    "``plays_coverage critical_only=true has_gaps=true`` → "
    "``play_automate`` per repo.\n"
    "- 'Why did run X fail and is there an open inbox item for it?' "
    "→ ``run_detail run_id=...`` → escalations → "
    "``inbox_get inbox_item_id=...``."
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
        out: list[ChatMessage] = [
            ChatMessage(role="system", content=_AGENT_SYSTEM_PROMPT),
        ]
        # Policies preamble lives between the static system prompt and
        # the dynamic per-thread context (topic summary / buckets / kb)
        # so that workspace-level invariants take precedence over the
        # warmed memory the agent might otherwise lean on. One DB read
        # per assemble — workspaces have a handful of policies at most,
        # so this is cheap enough not to need a memo cache.
        policies_preamble = await render_policies_preamble(
            self._session, self._workspace_id
        )
        if policies_preamble:
            out.append(
                ChatMessage(role="system", content=policies_preamble)
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
