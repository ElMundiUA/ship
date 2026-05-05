"""Soft-stale claims that no source has confirmed for a while.

Operators delete docs all the time — the runbook archive moves to a
different folder, the Notion page gets repurposed, the meeting notes
never get re-mentioned. Without a decay step the canon would carry
those claims forever, every search would surface "the deploy
runbook lives at /docs/old-deploy.md", and the operator would
slowly stop trusting the system.

The decay rule is intentionally simple:

- Pick ``active`` claims whose ``last_seen_at`` is older than
  ``_DECAY_TTL_DAYS``.
- Skip claims with ``superseded_by_id != NULL`` — those are already
  in a non-active state via the reconciler's supersedes path.
- Flip status to ``stale``. Don't delete, don't unlink source_links.

``last_seen_at`` is the canonical "any source still asserts this
fact" timestamp:

- The extractor's ``_persist_claim`` short-circuits on exact-text
  match and bumps ``last_seen_at = now`` whenever it sees the same
  claim text from any source — so a multi-source claim survives
  any one source disappearing.
- The reconciler bumps ``last_seen_at`` on the canon row whenever a
  duplicate or refines decision lands.
- Both paths above are workspace-local and idempotent, so the
  signal stays clean.

Auto-revive is the symmetric path: if a stale claim's text shows up
again from a source, the extractor (next phase of this PR's
companion edit) flips it back to ``active``. That makes the system
self-healing — a "deleted then restored" doc revives the canon
without operator intervention.

This module owns the cron-side flip; the auto-revive lives in
:mod:`backend.app.services.knowledge_claim_extractor` because
that's where ``last_seen_at`` is bumped on dedup hits. Keeping the
two split means the decay tick can run on a workspace that
currently has no LLM credentials configured (extractor is gated on
LLM, decay isn't).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    ClaimStatus,
    KnowledgeClaim,
)


log = logging.getLogger(__name__)


# Window after which an unconfirmed active claim becomes stale.
# Calibration: 30 days lines up with the typical "I haven't read
# this doc in a month, has it changed" cadence — long enough that
# vacation gaps don't trigger false positives, short enough that
# orphaned facts don't pollute search for a quarter. Operator can
# revive a stale claim, so the cost of an over-eager flip is
# minor; the cost of an under-eager one is misleading agent
# context, which is worse.
_DECAY_TTL_DAYS = 30


@dataclass(slots=True)
class DecayReport:
    """Outcome of one decay pass over a workspace."""

    workspace_id: uuid.UUID
    inspected: int = 0
    flipped_stale: int = 0


async def decay_workspace_claims(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ttl_days: int = _DECAY_TTL_DAYS,
    now: datetime | None = None,
) -> DecayReport:
    """Flip un-confirmed active claims to ``stale``.

    Caller owns the transaction. A bulk UPDATE keeps this O(1)
    round trips even when N is in the thousands; the partial index
    on ``status='active'`` (added in 0061's predecessor work) means
    Postgres scans only the active tail.

    ``now`` is parameterised for tests — production callers leave
    it None so the cutoff floats with wall-clock time.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=ttl_days)

    # We could split into a SELECT + per-row UPDATE for audit logs,
    # but the audit value would be "claim X went stale on Y" which
    # is already recoverable from ``last_seen_at`` + ``updated_at``.
    # A single bulk UPDATE keeps the cron tick predictable on
    # workspaces with thousands of claims.
    result = await session.execute(
        update(KnowledgeClaim)
        .where(KnowledgeClaim.workspace_id == workspace_id)
        .where(KnowledgeClaim.status == ClaimStatus.ACTIVE)
        .where(KnowledgeClaim.superseded_by_id.is_(None))
        .where(KnowledgeClaim.last_seen_at < cutoff)
        .values(status=ClaimStatus.STALE)
    )
    flipped = int(result.rowcount or 0)
    return DecayReport(
        workspace_id=workspace_id,
        inspected=flipped,  # bulk UPDATE only counts the rows it touched
        flipped_stale=flipped,
    )


__all__ = [
    "DecayReport",
    "decay_workspace_claims",
]
