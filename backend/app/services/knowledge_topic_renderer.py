"""Render a topic's active claims into a canonical markdown view.

Topic views are the post-claim-store retrieval surface: one row per
``(workspace, topic_tag)`` containing a coherent markdown article
rendered from the **currently active** claims that share that tag.

Why a derived view instead of letting agents query claims directly:

- Agents and operators want narratives. Search returning 30 atomic
  claims is correct but unergonomic — the reader still has to do
  the synthesis. A pre-rendered view gives them the article-shape
  output, while the underlying claim store remains the canonical
  fact graph for diff / supersedes / decay reasoning.
- Topics are multi-tag, so a single Linear-FSM claim shows up in
  both the ``integrations`` and ``architecture-decisions`` views.
  That redundancy is the point: each view is a perspective on the
  canon, not a partition of it.
- Cache invalidation is cheap. ``claim_set_sha`` hashes the sorted
  active claim ids feeding the view; if the cron tick computes the
  same sha next time, we skip the LLM call. So the per-tick cost
  is "topics whose claim set actually drifted", not "every topic
  in the workspace".

Threshold + caps:

- ``_MIN_CLAIMS_PER_TOPIC`` skips singleton tags. With identity
  thresholds at the extractor (one claim per Notion bullet line)
  it's easy to end up with 5000 single-claim topics; we render
  only ones that have enough density to merit a view.
- ``_MAX_CLAIMS_PER_VIEW`` clamps the prompt — past ~50 claims the
  LLM tends to lose coherence and quality drops faster than
  coverage rises. The reader can still drill into the underlying
  claim list via the read API.
- ``_MAX_TOPICS_PER_TICK`` keeps one cron tick bounded so a 200-
  topic workspace doesn't single-thread a sweep.

Graceful degradation:

- With an LLM client: full prose article via Haiku (cheap, 2k
  output tokens enough for a tight article on most topics).
- Without one: deterministic bullet-list fallback. Same
  ``claim_set_sha`` so the cache still works; when an LLM finally
  becomes available the next tick re-renders the same set with the
  upgraded body. No retry storm because the fallback render still
  stamps ``last_rendered_at`` and ``rendered_by_model='deterministic'``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    ClaimStatus,
    KnowledgeClaim,
    KnowledgeTopicView,
)
from backend.app.services.agent.client import AgentClient, ChatMessage


log = logging.getLogger(__name__)


# Topics with fewer claims than this aren't worth rendering — the
# article would be three bullets and the LLM would either pad or
# return half a sentence.
_MIN_CLAIMS_PER_TOPIC = 3

# Cap claims fed to the LLM. Past ~50 the article quality drops
# faster than coverage rises; the read API can still drill into the
# raw claim list when an operator wants the long tail.
_MAX_CLAIMS_PER_VIEW = 50

# Per-tick fan-out cap.
_MAX_TOPICS_PER_TICK = 50

# Identifies the "no LLM client" code path in the rendered_by_model
# column so dashboards can spot tenants that synth degraded.
_DETERMINISTIC_MODEL = "deterministic"


_RENDER_SYSTEM_PROMPT = """\
You produce a single canonical markdown article on the given topic
from a list of atomic claims.

Rules:

- The article must be coherent prose (or short bulleted lists where
  appropriate), not a regurgitation of the raw input.
- DO NOT invent. Every assertion must come from one of the listed
  claims. If you can't fit a claim into the article, drop it; never
  paraphrase beyond what the claim says.
- Order claims logically: facts and definitions first, rules /
  decisions next, examples last. Group related claims into sections.
- Start with ``# <Title>`` where Title is a short noun phrase
  capturing the topic.
- Don't reference "claims" or "the document" — write as if this is
  the original canonical reference.

Output ONLY the markdown article. No commentary, no JSON wrapper.
"""


@dataclass(slots=True)
class TopicRenderReport:
    """Outcome of one render call for a single topic."""

    topic_tag: str
    rendered: bool = False
    skipped_unchanged: bool = False
    skipped_low_density: bool = False
    used_llm: bool = False
    claim_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkspaceRenderReport:
    """Outcome of one cron tick over a workspace's eligible topics."""

    inspected: int = 0
    rendered: int = 0
    skipped_unchanged: int = 0
    skipped_low_density: int = 0
    failed: int = 0


def _claim_set_sha(active_claim_ids: list[uuid.UUID]) -> str:
    """Stable hash of the sorted active claim id set.

    UUID comparison is lexicographic on the canonical string form;
    sorting there gives us reproducible bytes-into-sha256 input
    regardless of the order Postgres returned the rows.
    """
    joined = ",".join(sorted(str(cid) for cid in active_claim_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _humanise_topic(topic_tag: str) -> str:
    """``linear-fsm`` → ``Linear FSM`` for the article title."""
    parts = [p for p in topic_tag.replace("_", "-").split("-") if p]
    return " ".join(p.capitalize() for p in parts) or topic_tag


def _deterministic_body(
    topic_tag: str, claims: list[KnowledgeClaim]
) -> tuple[str, str]:
    """Fallback render when no LLM is available — title + bullet body.

    Sorted by ``kind`` first (decisions / rules / facts cluster),
    then by claim text to keep the output stable for the same input.
    Same ``claim_set_sha`` as the LLM path so the cache invalidation
    invariant still holds; the next tick that *does* have a client
    will overwrite this body in place without growing the table.
    """
    title = _humanise_topic(topic_tag)
    kind_order = {"decision": 0, "rule": 1, "fact": 2, "example": 3, "glossary": 4, "other": 5}
    sorted_claims = sorted(
        claims, key=lambda c: (kind_order.get(c.kind, 99), c.claim_md)
    )
    lines = [f"# {title}", ""]
    for claim in sorted_claims:
        kind_badge = f"_{claim.kind}_" if claim.kind != "fact" else ""
        prefix = f"- {kind_badge}: " if kind_badge else "- "
        lines.append(f"{prefix}{claim.claim_md}")
    return title, "\n".join(lines).rstrip() + "\n"


async def _llm_render(
    *,
    client: AgentClient,
    topic_tag: str,
    claims: list[KnowledgeClaim],
) -> tuple[str, str] | None:
    """Ask the LLM to render the claims as one canonical article.

    Returns ``(title, body_md)`` or ``None`` on any failure (the
    caller falls back to the deterministic body so the cache stays
    consistent; the next tick will retry the LLM path).
    """
    title_hint = _humanise_topic(topic_tag)
    claim_lines = "\n".join(
        f"{i + 1}. ({claim.kind}) {claim.claim_md}"
        for i, claim in enumerate(claims)
    )
    user_msg = (
        f"Topic: {title_hint} (tag `{topic_tag}`)\n\n"
        f"Claims:\n{claim_lines}\n\n"
        f"Render the article."
    )
    try:
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_RENDER_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_msg),
            ],
            max_tokens=2000,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info(
            "topic_renderer: LLM render failed for topic=%s (%s)", topic_tag, exc
        )
        return None

    body = (raw or "").strip()
    if not body:
        return None
    # Strip a markdown code fence if the model wrapped its answer in
    # one — we want the article body raw, not embedded in a triple-
    # backtick block.
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("markdown\n"):
            body = body[len("markdown\n"):]
        body = body.strip("`").strip()
    # Title = first heading line if present, else humanised tag.
    title = title_hint
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip() or title_hint
            break
    return title[:512], body + ("\n" if not body.endswith("\n") else "")


# ---------------------------------------------------------------------------
# Per-topic + per-workspace entry points
# ---------------------------------------------------------------------------


async def render_topic(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    topic_tag: str,
    llm_client: AgentClient | None,
    settings: Settings | None = None,
) -> TopicRenderReport:
    """Render or refresh the ``(workspace, topic_tag)`` view.

    Caller owns the transaction; this function only flushes when it
    has work to commit. Cache hit (``claim_set_sha`` matches the
    existing row) is a no-op — no DB writes, no LLM call.
    """
    settings = settings or get_settings()
    report = TopicRenderReport(topic_tag=topic_tag)

    claims = (
        await session.execute(
            select(KnowledgeClaim)
            .where(KnowledgeClaim.workspace_id == workspace_id)
            .where(KnowledgeClaim.status == ClaimStatus.ACTIVE)
            .where(KnowledgeClaim.topic_tags.any(topic_tag))
            .order_by(KnowledgeClaim.created_at.asc())
            .limit(_MAX_CLAIMS_PER_VIEW)
        )
    ).scalars().all()
    report.claim_count = len(claims)

    if len(claims) < _MIN_CLAIMS_PER_TOPIC:
        report.skipped_low_density = True
        return report

    sha = _claim_set_sha([c.id for c in claims])

    existing = (
        await session.execute(
            select(KnowledgeTopicView).where(
                KnowledgeTopicView.workspace_id == workspace_id,
                KnowledgeTopicView.topic_tag == topic_tag,
            )
        )
    ).scalar_one_or_none()

    if existing is not None and existing.claim_set_sha == sha:
        report.skipped_unchanged = True
        return report

    title: str
    body: str
    rendered_by: str
    if llm_client is None:
        title, body = _deterministic_body(topic_tag, claims)
        rendered_by = _DETERMINISTIC_MODEL
    else:
        rendered = await _llm_render(
            client=llm_client, topic_tag=topic_tag, claims=claims
        )
        if rendered is None:
            title, body = _deterministic_body(topic_tag, claims)
            rendered_by = _DETERMINISTIC_MODEL
        else:
            title, body = rendered
            rendered_by = getattr(llm_client, "vendor", "llm")
            report.used_llm = True

    now = datetime.now(timezone.utc)
    if existing is None:
        view = KnowledgeTopicView(
            workspace_id=workspace_id,
            topic_tag=topic_tag,
            title=title,
            body_md=body,
            claim_set_sha=sha,
            claim_count=len(claims),
            rendered_by_model=rendered_by,
            last_rendered_at=now,
        )
        session.add(view)
    else:
        existing.title = title
        existing.body_md = body
        existing.claim_set_sha = sha
        existing.claim_count = len(claims)
        existing.rendered_by_model = rendered_by
        existing.last_rendered_at = now

    await session.flush()
    report.rendered = True
    return report


async def _eligible_topics(
    session: AsyncSession, *, workspace_id: uuid.UUID, limit: int
) -> list[str]:
    """Return topic_tags with at least ``_MIN_CLAIMS_PER_TOPIC`` active claims.

    Uses ``unnest(topic_tags)`` to flatten the multi-tag arrays then
    GROUP BY — Postgres can serve this from the GIN index on
    ``knowledge_claim.topic_tags`` without a sequential scan.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT tag, count(*) AS c
                FROM (
                  SELECT unnest(topic_tags) AS tag
                  FROM knowledge_claim
                  WHERE workspace_id = :ws AND status = :active
                ) sub
                WHERE tag IS NOT NULL AND tag <> ''
                GROUP BY tag
                HAVING count(*) >= :min_count
                ORDER BY c DESC, tag ASC
                LIMIT :lim
                """
            ),
            {
                "ws": str(workspace_id),
                "active": ClaimStatus.ACTIVE,
                "min_count": _MIN_CLAIMS_PER_TOPIC,
                "lim": limit,
            },
        )
    ).all()
    return [str(row[0]) for row in rows]


async def render_workspace_topics(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    llm_client: AgentClient | None,
    limit: int = _MAX_TOPICS_PER_TICK,
    settings: Settings | None = None,
) -> WorkspaceRenderReport:
    """Walk eligible topics for one workspace and render any that drifted.

    Eligible = topic has ≥ ``_MIN_CLAIMS_PER_TOPIC`` active claims.
    Each topic costs at most one LLM call per tick (cache-hit
    short-circuit covers the no-change case).
    """
    settings = settings or get_settings()
    report = WorkspaceRenderReport()

    topics = await _eligible_topics(
        session, workspace_id=workspace_id, limit=limit
    )
    report.inspected = len(topics)

    for tag in topics:
        try:
            outcome = await render_topic(
                session,
                workspace_id=workspace_id,
                topic_tag=tag,
                llm_client=llm_client,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 — never poison the tick
            log.exception(
                "topic_renderer: workspace=%s tag=%s failed", workspace_id, tag
            )
            report.failed += 1
            continue

        if outcome.rendered:
            report.rendered += 1
        elif outcome.skipped_unchanged:
            report.skipped_unchanged += 1
        elif outcome.skipped_low_density:
            report.skipped_low_density += 1

    return report


__all__ = [
    "TopicRenderReport",
    "WorkspaceRenderReport",
    "render_topic",
    "render_workspace_topics",
]
