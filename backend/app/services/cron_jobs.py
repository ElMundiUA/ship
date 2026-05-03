"""Concrete cron job registrations.

Imported once during FastAPI startup (see :mod:`backend.app.main`'s
lifespan). Each ``register_cron`` call queues a job; the actual
scheduler binding happens in :func:`start_scheduler`.

Adding a new cron is two lines:

  1. Reserve a new ``CronLockId`` in :mod:`services.cron`.
  2. Define an ``@cron_with_lock(lock=...)``-decorated coroutine and
     ``register_cron(...)`` it here.

Keep this file *thin*: the heavy lifting lives in service modules
(``knowledge_harvest`` etc.); this module is just wiring.
"""

from __future__ import annotations

import logging

from backend.app.core.config import get_settings
from backend.app.db.session import get_sessionmaker
from backend.app.services.agent.client import pick_default_client
from backend.app.services.cron import (
    CronLockId,
    cron_with_lock,
    register_cron,
)
from backend.app.services.knowledge_decay import gc_all_workspaces
from backend.app.services.knowledge_harvest import harvest_all_workspaces
from backend.app.services.knowledge_router import route_all_workspaces
from backend.app.services.knowledge_synth import synthesise_all_workspaces


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KB-1 / ELS-34 — knowledge-note harvester
# ---------------------------------------------------------------------------


@cron_with_lock(lock=CronLockId.KNOWLEDGE_HARVEST, name="knowledge_harvest")
async def _knowledge_harvest_tick() -> None:
    """Hourly sweep of resolved clarifications across every workspace.

    Reuses :func:`harvest_all_workspaces`. Best-effort LLM client —
    ``None`` is fine, the harvester falls back to the identity
    extractor on missing model creds. Wraps its own session for
    DB writes; the lock-side session that grants the advisory lock
    is held by the wrapper, not this body.
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_harvest tick: no LLM client configured; "
            "identity extractor only"
        )
        llm_client = None

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            reports = await harvest_all_workspaces(
                session, llm_client=llm_client
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    total_created = sum(r.created for r in reports)
    total_skipped = sum(r.skipped_duplicate for r in reports)
    if total_created or any(r.errors for r in reports):
        log.info(
            "knowledge_harvest tick: workspaces=%d created=%d skipped_dup=%d",
            len(reports),
            total_created,
            total_skipped,
        )


# ---------------------------------------------------------------------------
# KB-2 / ELS-36 — embed/centroid + LLM tiebreaker router
# ---------------------------------------------------------------------------


@cron_with_lock(lock=CronLockId.KNOWLEDGE_ROUTE, name="knowledge_route")
async def _knowledge_route_tick() -> None:
    """Hourly routing pass: pin every unrouted knowledge_note to a bucket.

    Centroid match → bucket_hint fallback → LLM tiebreaker. Notes
    that hit ``no_fit`` exit the pending pool with confidence=0 and
    KB-4 (operator review) handles them by hand.
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_route tick: no LLM client configured; "
            "centroid + bucket_hint only (no LLM tiebreaker)"
        )
        llm_client = None

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            reports = await route_all_workspaces(session, llm_client=llm_client)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    auto = sum(r.auto_pinned for r in reports)
    hint = sum(r.routed_via_hint for r in reports)
    llm_routed = sum(r.routed_via_llm for r in reports)
    no_fit = sum(r.no_fit for r in reports)
    embed_failed = sum(r.skipped_embed_failed for r in reports)
    if auto or hint or llm_routed or no_fit or any(r.errors for r in reports):
        log.info(
            "knowledge_route tick: workspaces=%d auto=%d hint=%d llm=%d "
            "no_fit=%d embed_failed=%d",
            len(reports),
            auto,
            hint,
            llm_routed,
            no_fit,
            embed_failed,
        )


# ---------------------------------------------------------------------------
# KB-3 / ELS-37 — daily synthesiser
# ---------------------------------------------------------------------------


@cron_with_lock(lock=CronLockId.KNOWLEDGE_SYNTH, name="knowledge_synth")
async def _knowledge_synth_tick() -> None:
    """Hourly draft-article producer.

    Per workspace, per bucket: collect every routed-but-not-
    synthesised note (capped), feed the LLM, write a single
    BucketArticle status='draft'. Embedding + bucket.updated_at
    bump are baked in (per the ELS-37 acceptance addendum).
    Without an LLM client the cron skips silently — notes stay
    pending until a tick with creds picks them up.
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_synth tick: no LLM client configured; "
            "synthesis skipped this tick"
        )
        llm_client = None

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            reports = await synthesise_all_workspaces(
                session, llm_client=llm_client
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    drafts = sum(r.drafts_created for r in reports)
    notes = sum(r.notes_consumed for r in reports)
    no_llm = sum(r.drafts_skipped_no_llm for r in reports)
    if drafts or notes or no_llm or any(r.errors for r in reports):
        log.info(
            "knowledge_synth tick: workspaces=%d drafts=%d notes_consumed=%d "
            "skipped_no_llm=%d",
            len(reports),
            drafts,
            notes,
            no_llm,
        )


# ---------------------------------------------------------------------------
# Step 6 — daily archive GC
# ---------------------------------------------------------------------------


@cron_with_lock(lock=CronLockId.KNOWLEDGE_DECAY, name="knowledge_decay")
async def _knowledge_decay_tick() -> None:
    """Daily sweep: hard-delete archived bucket articles past the TTL.

    Operator-driven archive (``POST /buckets/{slug}/archive``) and
    synthesiser-proposed archive both flip articles to ``status='archived'``
    with ``archived_at=now()``. This sweep collects rows older than
    ``ARCHIVE_TTL_DAYS`` and removes them so the table doesn't grow
    forever. ``BucketArticleSource`` rows cascade-delete via the FK.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            reports = await gc_all_workspaces(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    deleted = sum(r.deleted for r in reports)
    if deleted:
        log.info(
            "knowledge_decay tick: workspaces=%d deleted=%d",
            len(reports),
            deleted,
        )


def register_all() -> None:
    """Wire every cron defined in this module into the scheduler.

    Called once from :func:`backend.app.services.cron.start_scheduler`'s
    caller (FastAPI lifespan). Splitting this from the decorator
    application means the registrations are testable in isolation —
    a unit test can call ``register_all`` against a mock scheduler.
    """
    register_cron(
        fn=_knowledge_harvest_tick,
        cron_expr="20 * * * *",  # every hour at :20
        job_id="knowledge_harvest",
    )
    # Routing runs ten minutes after harvest so the cron-tick chain is
    # harvest → route → (KB-3 synthesise at :40 once it lands). One
    # workspace's tick rarely takes more than a few seconds, but the
    # offset gives breathing room.
    register_cron(
        fn=_knowledge_route_tick,
        cron_expr="30 * * * *",  # every hour at :30
        job_id="knowledge_route",
    )
    register_cron(
        fn=_knowledge_synth_tick,
        cron_expr="40 * * * *",  # every hour at :40
        job_id="knowledge_synth",
    )
    # GC runs once daily. Cheap query (filtered DELETE by archived_at)
    # so an off-peak slot is fine; pick 03:15 UTC to avoid the on-the-
    # hour pile-ups from the harvest/route/synth chain.
    register_cron(
        fn=_knowledge_decay_tick,
        cron_expr="15 3 * * *",  # daily at 03:15 UTC
        job_id="knowledge_decay",
    )


__all__ = ["register_all"]
