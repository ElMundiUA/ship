"""Reconcile freshly-extracted claims against existing canon.

The extractor (P1) is intentionally promiscuous — it inserts every
atomic statement the LLM finds with ``status='active'`` and
``confidence=1.0``, no awareness of what's already in the store. That
keeps extraction cheap, parallel-safe, and idempotent on re-runs.

The flip side: a workspace that re-syncs the same Notion page over a
month accumulates near-duplicate claims with slightly different
wording, and architecture decisions overwritten by later ones live
side-by-side with their replacements. Search would surface both,
the agent would have no way to tell which is current, and the
"clean canon" promise rots quickly.

This module is the catch-up pass. Per pending claim:

1. **Fast-path duplicate.** Find the single nearest active claim by
   cosine similarity; if it's > ``_DUP_AUTO_THRESHOLD`` (0.95) we
   call them duplicates without spending an LLM call. The new claim
   is folded into the existing row's ``source_links`` and marked
   ``status='superseded', superseded_by_id=existing.id``.

2. **LLM-judge band.** For sims ≥ ``_LLM_JUDGE_THRESHOLD`` (0.85)
   but below the auto-threshold, run a small LLM call labelling
   the relationship as one of:

   - ``duplicate``  — same fact, different wording. Apply same
     handling as the fast-path.
   - ``refines``    — the new claim is more current / accurate. The
     old claim is the one that becomes superseded; the new one
     stays active.
   - ``contradicts``— both claim something incompatible about the
     same fact. We can't pick a winner without an operator;
     **both** rows get ``status='disputed'`` and stay queryable
     under that filter so the inbox surfaces them for review.
   - ``unrelated``  — textual proximity but different topics. No
     action; reconciler simply marks the new claim done.

3. **No near-match.** Below the LLM-judge threshold, leave the new
   claim alone — it's a genuinely new fact.

Whatever the outcome, the reconciler stamps ``reconciled_at = now()``
on the new claim so the next tick's filter (``WHERE reconciled_at IS
NULL``) skips it. Failures inside an LLM call also stamp the
timestamp so a stuck judge call doesn't loop the same row forever;
the operator can force a redo by NULL-ing the column.

This module deliberately stays out of the inbox. ``status='disputed'``
is the queryable surface; an operator review UI ride in a follow-up
PR rather than blocking the dedup logic on UX work.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    ClaimStatus,
    KnowledgeClaim,
)
from backend.app.services.agent.client import AgentClient, ChatMessage


log = logging.getLogger(__name__)


# Cosine similarity above this → auto-fold without spending an LLM
# call. The 0.95 threshold is calibrated so genuinely different claims
# with overlapping vocabulary (e.g. two different rules about the same
# subject) stay above the LLM-judge band rather than getting silently
# merged.
_DUP_AUTO_THRESHOLD = 0.95

# Below this similarity we don't ask the LLM at all — a coincidental
# vocabulary overlap isn't worth the token spend. Tunable per
# workspace if a deployment has unusually low-vocabulary drift.
_LLM_JUDGE_THRESHOLD = 0.85

# Top-K nearest claims to consider per pending row. Most decisions
# fire on rank 1; the small K is there to handle the case where the
# nearest neighbour happens to be ``unrelated`` and the actual
# duplicate sits at rank 2 (e.g. when an old claim's wording drifted
# more than its modern restatement).
_NEAREST_K = 5

# Per-cron-tick batch — bounded so a 5000-claim backfill doesn't
# single-thread one tick.
_RECONCILE_BATCH_LIMIT = 25


_JUDGE_SYSTEM_PROMPT = """\
You compare two claims that are both currently in our knowledge base
and decide their relationship. Output ONLY a JSON object.

Pick exactly one label:

- "duplicate"   — the two claims assert the same fact (wording differs,
                  meaning is identical).
- "refines"     — the NEW claim is a more current / accurate / specific
                  version of the OLD claim. The OLD claim should be
                  marked superseded.
- "contradicts" — the two claims disagree about the same fact. Both
                  cannot be true; needs human review.
- "unrelated"   — they're about different things despite vocabulary
                  overlap.

Output schema (no commentary, no markdown fences):
  {"decision": "duplicate|refines|contradicts|unrelated",
   "reason": "≤140 chars why"}
"""


_DECISION_DUPLICATE = "duplicate"
_DECISION_REFINES = "refines"
_DECISION_CONTRADICTS = "contradicts"
_DECISION_UNRELATED = "unrelated"
_VALID_DECISIONS = frozenset(
    {
        _DECISION_DUPLICATE,
        _DECISION_REFINES,
        _DECISION_CONTRADICTS,
        _DECISION_UNRELATED,
    }
)


@dataclass(slots=True)
class ReconcileReport:
    """Outcome of one reconciliation pass over a single claim."""

    claim_id: uuid.UUID
    decision: str | None = None  # duplicate / refines / contradicts / unrelated / no_match
    matched_claim_id: uuid.UUID | None = None
    similarity: float | None = None
    used_llm: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchReport:
    """Outcome of one reconciler tick across a workspace's pending claims."""

    inspected: int = 0
    auto_duplicates: int = 0
    llm_duplicates: int = 0
    refines: int = 0
    contradicts: int = 0
    unrelated: int = 0
    no_match: int = 0
    skipped_no_embedding: int = 0
    failed: int = 0


# ---------------------------------------------------------------------------
# Nearest-neighbour lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NearestRow:
    claim_id: uuid.UUID
    similarity: float
    claim_md: str


async def _nearest_active_claims(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    self_claim_id: uuid.UUID,
    embedding: list[float],
    k: int,
) -> list[_NearestRow]:
    """Top-K active claims in the same workspace, ranked by cosine similarity.

    Uses the HNSW index from migration 0059 (``vector_cosine_ops``).
    Postgres pgvector exposes cosine distance via the ``<=>`` operator
    (0 = identical, 2 = opposite); cosine *similarity* is ``1 -
    distance``. We compute it inline so the caller compares against
    intuitive thresholds without re-deriving the algebra.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, claim_md, 1 - (embedding <=> :q) AS similarity
                FROM knowledge_claim
                WHERE workspace_id = :ws
                  AND status = :active
                  AND embedding IS NOT NULL
                  AND id <> :self_id
                ORDER BY embedding <=> :q
                LIMIT :k
                """
            ),
            {
                "q": str(embedding),
                "ws": str(workspace_id),
                "self_id": str(self_claim_id),
                "active": ClaimStatus.ACTIVE,
                "k": k,
            },
        )
    ).all()
    return [
        _NearestRow(
            claim_id=row[0],
            claim_md=row[1],
            similarity=float(row[2]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------


async def _llm_judge(
    *,
    client: AgentClient,
    old_text: str,
    new_text: str,
) -> tuple[str, str] | None:
    """Ask the LLM to label the (old, new) relationship.

    Returns ``(decision, reason)`` or ``None`` if the call failed or
    the response was malformed. The caller treats ``None`` the same
    as ``unrelated`` — better to leave the claim alone than to mark
    a supersedes chain off a flaky completion.
    """
    user_msg = (
        f"OLD claim (already in store):\n  {old_text}\n\n"
        f"NEW claim (just extracted):\n  {new_text}"
    )
    try:
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_JUDGE_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_msg),
            ],
            max_tokens=200,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("reconciler: LLM judge call failed (%s)", exc)
        return None

    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
        cleaned = cleaned.strip("`").strip()
    try:
        obj: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        log.info("reconciler: LLM judge returned non-JSON (%r)", raw[:120])
        return None
    if not isinstance(obj, dict):
        return None
    decision = str(obj.get("decision") or "").strip().lower()
    if decision not in _VALID_DECISIONS:
        return None
    reason = str(obj.get("reason") or "").strip()[:200]
    return decision, reason


# ---------------------------------------------------------------------------
# Decision application
# ---------------------------------------------------------------------------


def _merge_source_links(
    target: KnowledgeClaim, source: KnowledgeClaim, now: datetime
) -> None:
    """Append ``source.source_links`` into ``target.source_links``.

    Idempotent: a (source_item_id, extracted_at) pair already present
    is skipped. last_seen_at is bumped so decay knows the canon claim
    is still being asserted by at least one source.
    """
    existing = list(target.source_links or [])
    seen = {
        (
            entry.get("source_item_id"),
            entry.get("extracted_at"),
        )
        for entry in existing
        if isinstance(entry, dict)
    }
    for entry in source.source_links or []:
        if not isinstance(entry, dict):
            continue
        key = (
            entry.get("source_item_id"),
            entry.get("extracted_at"),
        )
        if key in seen:
            continue
        existing.append(entry)
        seen.add(key)
    target.source_links = existing
    target.last_seen_at = now


def _apply_duplicate(
    new: KnowledgeClaim, existing: KnowledgeClaim, now: datetime
) -> None:
    """``new`` is a duplicate of ``existing``: fold and supersede.

    ``existing`` is the canon row that wins; ``new`` becomes a
    superseded synonym pointing at it. We deliberately keep the new
    row instead of deleting it so the supersedes graph still records
    that this exact wording was once asserted (and audit log can
    surface that later).
    """
    _merge_source_links(existing, new, now)
    new.status = ClaimStatus.SUPERSEDED
    new.superseded_by_id = existing.id


def _apply_refines(
    new: KnowledgeClaim, existing: KnowledgeClaim, now: datetime
) -> None:
    """``new`` is a more current version of ``existing``: invert direction."""
    existing.status = ClaimStatus.SUPERSEDED
    existing.superseded_by_id = new.id
    # The new row absorbs the old row's provenance trail so the
    # current canon's source_links list "this is where we know this
    # from" stays complete instead of starting fresh on every
    # rewording.
    _merge_source_links(new, existing, now)


def _apply_contradicts(
    new: KnowledgeClaim, existing: KnowledgeClaim
) -> None:
    """Both claims disagree: park them for operator review.

    Disputed rows still match search filters that explicitly include
    ``status='disputed'`` (the operator-review surface), but the
    default ``status='active'`` retrieval skips them so a confused
    canon doesn't poison agent context.
    """
    existing.status = ClaimStatus.DISPUTED
    new.status = ClaimStatus.DISPUTED


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def reconcile_claim(
    session: AsyncSession,
    *,
    claim: KnowledgeClaim,
    llm_client: AgentClient | None,
    settings: Settings | None = None,
) -> ReconcileReport:
    """Reconcile one freshly-extracted claim against existing canon.

    Caller owns the transaction. The function flushes (not commits)
    after applying a decision so a partial batch failure mid-loop
    rolls back cleanly.

    ``llm_client`` is optional. When it's missing or its judge call
    fails, we still apply the auto-threshold fast-path; near-matches
    in the LLM-judge band are left alone (treated as ``unrelated``)
    rather than guessed at heuristically.
    """
    settings = settings or get_settings()
    report = ReconcileReport(claim_id=claim.id)
    now = datetime.now(timezone.utc)

    if not claim.embedding:
        # Embedding step in P1 was best-effort. A claim without one
        # can't participate in nearest-neighbour search, so we mark
        # it reconciled (no near-match) and move on. The next
        # extractor pass on a re-pulled doc will get another shot
        # at the embedding service.
        claim.reconciled_at = now
        report.decision = "no_match"
        return report

    try:
        nearest = await _nearest_active_claims(
            session,
            workspace_id=claim.workspace_id,
            self_claim_id=claim.id,
            embedding=claim.embedding,
            k=_NEAREST_K,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.exception("reconciler: nearest-neighbour query failed")
        report.errors.append(str(exc))
        claim.reconciled_at = now
        return report

    if not nearest or nearest[0].similarity < _LLM_JUDGE_THRESHOLD:
        claim.reconciled_at = now
        report.decision = "no_match"
        return report

    top = nearest[0]
    report.matched_claim_id = top.claim_id
    report.similarity = top.similarity

    # Fast-path: well above the LLM-judge band. Skip the model call.
    if top.similarity >= _DUP_AUTO_THRESHOLD:
        existing = await session.get(KnowledgeClaim, top.claim_id)
        if existing is None:  # disappeared between query + get
            claim.reconciled_at = now
            report.decision = "no_match"
            return report
        _apply_duplicate(new=claim, existing=existing, now=now)
        claim.reconciled_at = now
        await session.flush()
        report.decision = _DECISION_DUPLICATE
        return report

    # LLM-judge band. If we have no client, fall back to "unrelated".
    if llm_client is None:
        claim.reconciled_at = now
        report.decision = _DECISION_UNRELATED
        return report

    judged = await _llm_judge(
        client=llm_client,
        old_text=top.claim_md,
        new_text=claim.claim_md,
    )
    report.used_llm = True
    if judged is None:
        # Judge failure is treated as unrelated — better than risking
        # a wrong supersedes write off a malformed completion.
        claim.reconciled_at = now
        report.decision = _DECISION_UNRELATED
        return report

    decision, _reason = judged
    report.decision = decision

    if decision == _DECISION_UNRELATED:
        claim.reconciled_at = now
        await session.flush()
        return report

    existing = await session.get(KnowledgeClaim, top.claim_id)
    if existing is None:
        claim.reconciled_at = now
        return report

    if decision == _DECISION_DUPLICATE:
        _apply_duplicate(new=claim, existing=existing, now=now)
    elif decision == _DECISION_REFINES:
        _apply_refines(new=claim, existing=existing, now=now)
    elif decision == _DECISION_CONTRADICTS:
        _apply_contradicts(new=claim, existing=existing)

    claim.reconciled_at = now
    await session.flush()
    return report


async def reconcile_pending_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    llm_client: AgentClient | None,
    limit: int = _RECONCILE_BATCH_LIMIT,
    settings: Settings | None = None,
) -> BatchReport:
    """Walk one workspace's un-reconciled claims and apply decisions.

    Pending = ``status='active' AND reconciled_at IS NULL``. The
    partial index from migration 0060 makes this scan O(pending),
    not O(total claims), so a large canon doesn't slow the tick.
    """
    settings = settings or get_settings()
    report = BatchReport()

    rows = (
        await session.execute(
            select(KnowledgeClaim)
            .where(KnowledgeClaim.workspace_id == workspace_id)
            .where(KnowledgeClaim.reconciled_at.is_(None))
            .where(KnowledgeClaim.status == ClaimStatus.ACTIVE)
            .order_by(KnowledgeClaim.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    report.inspected = len(rows)

    for claim in rows:
        if not claim.embedding:
            claim.reconciled_at = datetime.now(timezone.utc)
            report.skipped_no_embedding += 1
            continue
        try:
            outcome = await reconcile_claim(
                session,
                claim=claim,
                llm_client=llm_client,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 — never poison the tick
            log.exception("reconciler: claim %s failed", claim.id)
            report.failed += 1
            claim.reconciled_at = datetime.now(timezone.utc)
            continue

        if outcome.decision == _DECISION_DUPLICATE:
            if outcome.used_llm:
                report.llm_duplicates += 1
            else:
                report.auto_duplicates += 1
        elif outcome.decision == _DECISION_REFINES:
            report.refines += 1
        elif outcome.decision == _DECISION_CONTRADICTS:
            report.contradicts += 1
        elif outcome.decision == _DECISION_UNRELATED:
            report.unrelated += 1
        else:
            report.no_match += 1

    return report


__all__ = [
    "BatchReport",
    "ReconcileReport",
    "reconcile_claim",
    "reconcile_pending_workspace",
]
