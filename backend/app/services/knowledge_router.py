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
# Minimum cosine score for the fallback "ship to nearest centroid when
# LLM says no_fit" path. Below this we genuinely have no signal — the
# note's vector is roughly orthogonal to every populated bucket — and
# emitting a low-confidence pin would mislead operator review more than
# it helps. Above this there's at least directional overlap, which the
# synthesiser can turn into a draft for human triage.
CENTROID_FALLBACK_MIN = 0.30
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
    """A workspace bucket the router can route into.

    ``centroid`` is ``None`` for buckets with zero embedded published
    articles — the bucket is description-only. Such buckets can't
    auto-pin (we have no vector to compare against), but they're still
    valid targets for ``bucket_hint`` matches and the LLM tiebreaker
    (which uses ``description`` to decide fit).
    """

    bucket_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    centroid: list[float] | None
    sample_count: int


@dataclass(slots=True)
class RouteReport:
    workspace_id: uuid.UUID
    inspected: int = 0
    auto_pinned: int = 0
    routed_via_hint: int = 0
    routed_via_llm: int = 0
    routed_via_centroid_fallback: int = 0
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
        # Workspace has zero buckets at all — there's literally nothing
        # to route into. (Buckets that exist but have no articles are
        # NOT this branch; they're still candidates via description-only
        # routing — see ``_bucket_centroids``.)
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

        bucket, confidence, source, details = decision

        # Persist the route into context. Bucket-id may be None when
        # source == 'no_fit' — KB-4 picks those up as "needs human".
        ctx = dict(note.context or {})
        ctx["routed_bucket_id"] = str(bucket.bucket_id) if bucket else None
        ctx["route_confidence"] = round(confidence, 4)
        ctx["route_source"] = source
        ctx["routed_at"] = datetime.now(timezone.utc).isoformat()
        if source == "no_fit" and details.get("no_fit_reason"):
            ctx["no_fit_reason"] = details["no_fit_reason"]
        else:
            # A note can leave the no_fit pool on a later tick (e.g. once
            # bucket descriptions improve, or when this fix loosens the
            # tiebreaker). Drop the stale reason from a previous run so
            # operator review doesn't see ``source=llm_tiebreaker`` next
            # to a leftover ``no_fit_reason='llm_call_failed'``.
            ctx.pop("no_fit_reason", None)
        note.context = ctx

        # Counters
        if source == "auto_pin":
            report.auto_pinned += 1
        elif source == "bucket_hint":
            report.routed_via_hint += 1
        elif source == "llm_tiebreaker":
            report.routed_via_llm += 1
        elif source == "centroid_fallback":
            report.routed_via_centroid_fallback += 1
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
                    **details,
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
) -> tuple[BucketCentroid | None, float, str, dict[str, Any]] | None:
    """Decide which bucket the note goes to.

    Returns ``(bucket | None, confidence, source, details)`` or ``None``
    when the embed step failed (caller leaves the row pending so the next
    tick retries). ``details`` carries diagnostics that end up in the
    audit payload — most importantly ``no_fit_reason`` when ``source``
    lands on ``no_fit`` so prod can be debugged without log access.
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

    populated = [b for b in centroids if b.centroid is not None]
    top_score = 0.0
    top: BucketCentroid | None = None
    if populated:
        scored = sorted(
            ((b, _cosine(note_vec, b.centroid or [])) for b in populated),
            key=lambda x: x[1],
            reverse=True,
        )
        top, top_score = scored[0]
        if top_score >= AUTO_PIN_THRESHOLD:
            return top, top_score, "auto_pin", {}

    # Centroid scoring was ambiguous (or every bucket is description-only);
    # check the LLM extractor's hint first against ALL candidates.
    hint = (note.context or {}).get("bucket_hint")
    if isinstance(hint, str):
        match = next((b for b in centroids if b.slug == hint), None)
        if match is not None:
            return match, HINT_CONFIDENCE, "bucket_hint", {}

    # Last resort: tiebreaker LLM. Sees ALL buckets (populated or not)
    # via their descriptions, so a brand-new workspace whose buckets are
    # description-only still gets a routing decision instead of stuck
    # "no_fit" forever. Falls back to "no_fit" on any failure.
    details: dict[str, Any] = {
        "centroid_top_score": round(top_score, 4),
        "candidate_buckets": len(centroids),
        "populated_buckets": len(populated),
    }
    if llm_client is None:
        details["no_fit_reason"] = "no_llm_client"
        # Fall through to centroid fallback below — even cheap "looks like
        # X" beats stalling forever in no_fit.
    else:
        slug, conf, llm_reason = await _llm_tiebreaker(
            note=note, centroids=centroids, client=llm_client
        )
        if slug is not None:
            chosen = next((b for b in centroids if b.slug == slug), None)
            if chosen is not None:
                return chosen, conf, "llm_tiebreaker", {}
            details["no_fit_reason"] = "llm_unknown_slug"
            details["llm_returned_slug"] = slug
        else:
            details["no_fit_reason"] = llm_reason

    # Centroid fallback: if cosine had any signal at all, ship the note to
    # the closest bucket as a low-confidence pin instead of leaving it
    # stranded. The synthesiser will still write a draft, KB-4 review
    # surfaces it for the operator to override — and that's strictly
    # better than 96% of notes vanishing into a "needs human" pool that
    # nobody reads. We label the source as ``centroid_fallback`` so the
    # audit log and operator-review UI can sort these distinctly.
    if top is not None and top_score >= CENTROID_FALLBACK_MIN:
        return top, top_score, "centroid_fallback", details

    return None, 0.0, "no_fit", details


# ---------------------------------------------------------------------------
# Centroids
# ---------------------------------------------------------------------------


async def _bucket_centroids(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[BucketCentroid]:
    """Pull every workspace-scoped bucket and (when possible) its centroid.

    Description-only buckets (zero embedded published articles) are still
    returned, with ``centroid=None``. They can't auto-pin via cosine
    similarity, but they're valid targets for ``bucket_hint`` matches and
    the LLM tiebreaker (which uses ``description`` to decide fit). This
    is what unblocks brand-new workspaces whose preset buckets ship with
    descriptions but no seed articles — without it, every note routes to
    ``no_fit`` forever and the synthesiser never runs.
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
        centroid = _mean_vector(vectors) if vectors else None
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
of a workspace's knowledge catalogue. Pick the slug that overlaps the
note's topic the most.

Rules:
- Only use slugs that appear in the bucket catalogue below. Inventing
  slugs returns ``no_fit``.
- Match aggressively. If the note plausibly belongs to a bucket based
  on the bucket's description, route it. Operator review (KB-4) is the
  safety net for misroutes — leaving notes in ``no_fit`` is worse than
  a slightly imperfect bucket.
- Reserve ``no_fit`` for notes that are genuinely off-topic for every
  bucket (e.g. a billing email in a knowledge base about engineering
  practices), not for notes that only loosely match.
- Confidence is a number in [0, 1]; reflect how sure you are. Use 0.5–0.7
  for "fits the bucket's topic but tangentially", 0.85+ for "central to
  the bucket's stated purpose".

Return strictly:
{"slug": "<bucket-slug>" | null, "confidence": 0.0-1.0}
"""


async def _llm_tiebreaker(
    *,
    note: Improvement,
    centroids: list[BucketCentroid],
    client: AgentClient,
) -> tuple[str | None, float, str]:
    """Ask a fast model to pick a slug.

    Returns ``(slug | None, confidence, reason)``. ``reason`` is one of
    ``llm_returned_null`` / ``llm_call_failed`` / ``llm_malformed`` /
    ``llm_unknown_slug`` when ``slug`` is ``None`` — surfaced into the
    audit log so prod can be debugged without log access.
    """
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
        # Don't hardcode the model — pass ``None`` so the client uses its
        # vendor-resolved fast model. ``Settings._validate_anthropic_models``
        # already maps the OpenAI-shaped default (``gpt-4o-mini``) to the
        # Anthropic equivalent when ``AGENT_VENDOR=anthropic``; hardcoding
        # an OpenAI name here was sending the request to Anthropic with
        # an unknown model id, which prod returned as a 4xx and we
        # caught as ``llm_call_failed`` for every note.
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_LLM_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_msg),
            ],
            max_tokens=200,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_router: LLM tiebreaker call failed (%s)", exc)
        return None, 0.0, "llm_call_failed"

    try:
        obj = _parse_route_json(raw)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_router: LLM tiebreaker malformed (%s)", exc)
        return None, 0.0, "llm_malformed"

    slug = obj.get("slug")
    if slug is None:
        return None, 0.0, "llm_returned_null"
    if not isinstance(slug, str) or slug not in {b.slug for b in centroids}:
        return None, 0.0, "llm_unknown_slug"
    conf_raw = obj.get("confidence")
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return slug, conf, "llm_routed"


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
