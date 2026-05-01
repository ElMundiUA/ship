"""Phase-2 router for the knowledge-ingestion epic (ELS-36 / KB-2).

Reads pending ``Improvement(kind='knowledge_note', routed_bucket_id=null)``
rows and decides which workspace bucket each one belongs in. Hybrid
strategy:

1. **Embed** the note body once. Skip rows whose embed call fails
   (we leave them pending — next tick retries when the model warms up).
2. **Centroid match.** For every workspace-scoped bucket with at least
   one embedded published article, compute the centroid (mean of those
   articles' vectors) and cosine-similarity vs the note. The highest-
   scoring bucket wins if its similarity beats ``AUTO_PIN_THRESHOLD``.
3. **Bucket hint** from KB-1b. The LLM extractor stamps an optional
   ``context.bucket_hint`` slug on each atom. When centroid is
   ambiguous (top score below threshold) but the hint matches a real
   workspace bucket, we route there with a recorded
   ``route_confidence`` mid-band so the operator-review surface
   (KB-4) can sort "very confident → asked LLM tiebreaker → hint
   only" naturally.
4. **LLM tiebreaker.** When everything else fails, ask a fast model
   to pick a slug from the catalogue (or ``no_fit``).
5. **No fit.** Mark the note ``routed_bucket_id=null`` AND
   ``route_confidence=0.0`` so it leaves the pending pool — KB-4 can
   surface it as "needs human classification".

All writes happen on the caller's session; this module never commits.

Cron entry point lives in :mod:`backend.app.services.cron_jobs`; this
module is just the work-doing logic so unit tests can drive it
directly without spinning up APScheduler.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.agent.embedding import embed_text
from backend.app.services.knowledge_harvest import NOTE_KIND


log = logging.getLogger(__name__)


# Cosine similarity above this score → auto-pin to the top bucket
# without consulting the LLM. Tuned conservatively: anything closer
# than 0.75 in cosine space is "obviously the same neighbourhood".
AUTO_PIN_THRESHOLD = 0.75
# When the LLM extractor's bucket_hint matches a real workspace
# bucket but centroid was ambiguous, we still route — but at a
# confidence band that lets KB-4 sort hint-only routes after the
# auto-pin band.
HINT_CONFIDENCE = 0.55
# Per-tick safety: stop the cron from grinding through a million
# pending notes if a backlog accumulates. The next tick picks up
# the rest.
ROUTE_BATCH_LIMIT = 200
# Cap how much of the note body we feed the LLM tiebreaker.
_LLM_NOTE_BODY_CAP = 4000


@dataclass(slots=True)
class BucketCentroid:
    bucket_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    centroid: list[float]
    sample_count: int


@dataclass(slots=True)
class RouteReport:
    workspace_id: uuid.UUID
    inspected: int = 0
    auto_pinned: int = 0
    routed_via_hint: int = 0
    routed_via_llm: int = 0
    no_fit: int = 0
    skipped_no_buckets: int = 0
    skipped_embed_failed: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def route_pending_notes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    llm_client: AgentClient | None = None,
    limit: int = ROUTE_BATCH_LIMIT,
) -> RouteReport:
    """Route every unrouted knowledge_note for one workspace.

    Returns a :class:`RouteReport` with counts; never raises (per-row
    errors land in ``report.errors``). Caller owns the transaction —
    a partial failure mid-batch rolls back cleanly.
    """
    report = RouteReport(workspace_id=workspace_id)

    centroids = await _bucket_centroids(session, workspace_id=workspace_id)

    pending_stmt = (
        select(Improvement)
        .where(Improvement.workspace_id == workspace_id)
        .where(Improvement.kind == NOTE_KIND)
        # context->>'routed_bucket_id' IS NULL captures both "never
        # routed" and "explicitly cleared by an operator". The atom
        # is in the pending pool either way.
        .where(Improvement.context["routed_bucket_id"].astext.is_(None))
        .order_by(Improvement.created_at.asc())
        .limit(limit)
    )
    pending = list((await session.execute(pending_stmt)).scalars().all())
    report.inspected = len(pending)

    if not pending:
        return report

    if not centroids:
        # Nothing to route into — but this shouldn't be a hard skip;
        # KB-1's notes still want their tick counted so the report
        # surfaces "you have notes but no buckets" cleanly.
        report.skipped_no_buckets = len(pending)
        return report

    for note in pending:
        try:
            decision = await _route_one(
                note=note,
                centroids=centroids,
                llm_client=llm_client,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per row
            log.exception("knowledge_router: row %s failed", note.id)
            report.errors.append(f"{note.id}: {exc}")
            continue

        if decision is None:
            report.skipped_embed_failed += 1
            continue

        bucket, confidence, source = decision

        # Persist the route into context. Bucket-id may be None when
        # source == 'no_fit' — KB-4 picks those up as "needs human".
        ctx = dict(note.context or {})
        ctx["routed_bucket_id"] = str(bucket.bucket_id) if bucket else None
        ctx["route_confidence"] = round(confidence, 4)
        ctx["route_source"] = source
        ctx["routed_at"] = datetime.now(timezone.utc).isoformat()
        note.context = ctx

        # Counters
        if source == "auto_pin":
            report.auto_pinned += 1
        elif source == "bucket_hint":
            report.routed_via_hint += 1
        elif source == "llm_tiebreaker":
            report.routed_via_llm += 1
        else:
            report.no_fit += 1

        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action="knowledge.note.routed",
                target_kind="improvement",
                target_id=str(note.id),
                payload={
                    "bucket_slug": bucket.slug if bucket else None,
                    "confidence": round(confidence, 4),
                    "source": source,
                },
            )
        )

    await session.flush()
    return report


async def route_all_workspaces(
    session: AsyncSession,
    *,
    llm_client: AgentClient | None = None,
    limit_per_workspace: int = ROUTE_BATCH_LIMIT,
) -> list[RouteReport]:
    """Cron entry point — sweep every workspace once."""
    workspace_ids = (
        await session.execute(select(Workspace.id))
    ).scalars().all()

    reports: list[RouteReport] = []
    for ws_id in workspace_ids:
        try:
            r = await route_pending_notes(
                session,
                workspace_id=ws_id,
                llm_client=llm_client,
                limit=limit_per_workspace,
            )
        except Exception as exc:
            log.exception("knowledge_router: workspace=%s failed", ws_id)
            r = RouteReport(workspace_id=ws_id, errors=[str(exc)])
        reports.append(r)
    return reports


# ---------------------------------------------------------------------------
# Per-row decision
# ---------------------------------------------------------------------------


async def _route_one(
    *,
    note: Improvement,
    centroids: list[BucketCentroid],
    llm_client: AgentClient | None,
) -> tuple[BucketCentroid | None, float, str] | None:
    """Decide which bucket the note goes to.

    Returns ``(bucket | None, confidence, source)`` or ``None`` when
    the embed step failed (caller leaves the row pending so the next
    tick retries).
    """
    text_to_embed = f"{note.title}\n\n{note.body or ''}"
    try:
        note_vec = await embed_text(text_to_embed)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info(
            "knowledge_router: embed failed for note=%s (%s); leaving pending",
            note.id,
            exc,
        )
        return None
    if not note_vec:
        return None

    scored = sorted(
        (
            (b, _cosine(note_vec, b.centroid))
            for b in centroids
        ),
        key=lambda x: x[1],
        reverse=True,
    )
    top, top_score = scored[0]

    if top_score >= AUTO_PIN_THRESHOLD:
        return top, top_score, "auto_pin"

    # Centroid was ambiguous; check the LLM extractor's hint first.
    hint = (note.context or {}).get("bucket_hint")
    if isinstance(hint, str):
        match = next((b for b in centroids if b.slug == hint), None)
        if match is not None:
            return match, HINT_CONFIDENCE, "bucket_hint"

    # Last resort: tiebreaker LLM. Falls back to "no_fit" on any
    # failure or if the model returns null.
    if llm_client is not None:
        slug, conf = await _llm_tiebreaker(
            note=note, centroids=centroids, client=llm_client
        )
        if slug is not None:
            chosen = next((b for b in centroids if b.slug == slug), None)
            if chosen is not None:
                return chosen, conf, "llm_tiebreaker"

    return None, 0.0, "no_fit"


# ---------------------------------------------------------------------------
# Centroids
# ---------------------------------------------------------------------------


async def _bucket_centroids(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[BucketCentroid]:
    """Pull every workspace-scoped bucket and compute its centroid.

    A bucket with zero embedded published articles produces no
    centroid (it's invisible to the router until it has at least one
    representative article). KB-4 review will surface notes that hit
    a freshly-empty bucket as "no_fit".
    """
    bucket_rows = (
        await session.execute(
            select(KnowledgeBucket)
            .where(KnowledgeBucket.workspace_id == workspace_id)
            .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
            .where(KnowledgeBucket.archived_at.is_(None))
        )
    ).scalars().all()

    centroids: list[BucketCentroid] = []
    for bucket in bucket_rows:
        article_rows = (
            await session.execute(
                select(BucketArticle.embedding)
                .where(BucketArticle.bucket_id == bucket.id)
                .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
                .where(BucketArticle.archived_at.is_(None))
                .where(BucketArticle.embedding.isnot(None))
            )
        ).scalars().all()
        vectors = [list(v) for v in article_rows if v is not None]
        if not vectors:
            continue
        centroid = _mean_vector(vectors)
        centroids.append(
            BucketCentroid(
                bucket_id=bucket.id,
                slug=bucket.slug,
                name=bucket.name,
                description=bucket.description,
                centroid=centroid,
                sample_count=len(vectors),
            )
        )
    return centroids


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


def _mean_vector(vectors: Sequence[Sequence[float]]) -> list[float]:
    n = len(vectors)
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += float(x)
    return [x / n for x in out]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        x = float(x)
        y = float(y)
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# LLM tiebreaker
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = """You route a knowledge note into the best-fitting bucket
of a workspace's knowledge catalogue. Pick the slug that fits, or
``no_fit`` if none does.

Rules:
- Only use slugs that appear in the bucket catalogue below. Inventing
  slugs returns ``no_fit``.
- Prefer ``no_fit`` over a forced match; the operator-review pass
  (KB-4) will route those by hand.
- Confidence is a number in [0, 1]; reflect how sure you are. Below
  0.5 still routes (the operator can override) but above 0.85 is
  treated as a strong signal downstream.

Return strictly:
{"slug": "<bucket-slug>" | null, "confidence": 0.0-1.0}
"""


async def _llm_tiebreaker(
    *,
    note: Improvement,
    centroids: list[BucketCentroid],
    client: AgentClient,
) -> tuple[str | None, float]:
    """Ask a fast model to pick a slug. Returns ``(slug | None, confidence)``."""
    catalogue = "\n".join(
        f"- `{b.slug}` — {b.name}: {(b.description or '').strip() or '(no description)'}"
        for b in centroids
    ) or "- (no buckets)"
    body = (note.body or "")[:_LLM_NOTE_BODY_CAP]
    user_msg = (
        f"## Bucket catalogue\n{catalogue}\n\n"
        f"## Note title\n{note.title}\n\n"
        f"## Note body\n{body}"
    )
    try:
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_LLM_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_msg),
            ],
            model="gpt-4o-mini",
            max_tokens=200,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_router: LLM tiebreaker call failed (%s)", exc)
        return None, 0.0

    try:
        obj = _parse_route_json(raw)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_router: LLM tiebreaker malformed (%s)", exc)
        return None, 0.0

    slug = obj.get("slug")
    if not isinstance(slug, str) or slug not in {b.slug for b in centroids}:
        return None, 0.0
    conf_raw = obj.get("confidence")
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return slug, conf


def _parse_route_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


__all__ = [
    "AUTO_PIN_THRESHOLD",
    "HINT_CONFIDENCE",
    "BucketCentroid",
    "RouteReport",
    "route_all_workspaces",
    "route_pending_notes",
]
