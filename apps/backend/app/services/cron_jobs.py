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
from backend.app.db.models.tenancy import Workspace
from backend.app.services.knowledge_claim_extractor import (
    extract_pending_workspace,
)
from backend.app.services.knowledge_decay import gc_all_workspaces
from backend.app.services.knowledge_harvest import harvest_all_workspaces
from backend.app.services.knowledge_ingestion import sync_due_import_sources
from backend.app.services.knowledge_reconciler import (
    reconcile_pending_workspace,
)
from backend.app.services.knowledge_router import route_all_workspaces
from backend.app.services.knowledge_synth import synthesise_all_workspaces
from backend.app.services.knowledge_claim_decay import decay_workspace_claims
from backend.app.services.knowledge_topic_renderer import (
    render_workspace_topics,
)
from sqlalchemy import select


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
# Knowledge import sources — periodic sync
# ---------------------------------------------------------------------------


@cron_with_lock(
    lock=CronLockId.KNOWLEDGE_SOURCES_SYNC, name="knowledge_sources_sync"
)
async def _knowledge_sources_sync_tick() -> None:
    """Every 15 min: re-sync ``KnowledgeImportSource`` rows that are due.

    Without this tick, ingestion only ran when an operator opened the
    sources page in the console (the GET endpoint scheduled
    ``sync_due_import_sources`` as a BackgroundTask). Operators in
    closed beta who left the page closed for a day saw their Notion
    / Confluence / website connectors quietly stop pulling fresh
    content. The cron makes the schedule load-bearing instead of
    UI-driven.

    ``sync_due_import_sources`` opens its own session per workspace
    so one bad source doesn't poison the rest, and its internal cap
    keeps each tick bounded.
    """
    result = await sync_due_import_sources(limit=20)
    if result.checked or result.errors:
        log.info("knowledge_sources_sync tick: %s", result.to_dict())


# ---------------------------------------------------------------------------
# Claim store — extractor + reconciler (post-0059)
# ---------------------------------------------------------------------------


async def _all_workspace_ids() -> list[Any]:
    """Fetch every workspace id once per tick — both claim ticks fan
    out per-workspace because ``extract_pending_workspace`` and
    ``reconcile_pending_workspace`` cap their batches to a workspace's
    own pending queue."""
    sm = get_sessionmaker()
    async with sm() as session:
        rows = (
            await session.execute(select(Workspace.id))
        ).scalars().all()
    return list(rows)


@cron_with_lock(
    lock=CronLockId.KNOWLEDGE_CLAIM_EXTRACT, name="knowledge_claim_extract"
)
async def _knowledge_claim_extract_tick() -> None:
    """Every 20 min: turn un-extracted source items into claims.

    Per-workspace fan-out so one big workspace's body doesn't starve
    the rest. The extractor itself is best-effort — a malformed LLM
    response stamps the item and moves on, and the next body change
    queues a re-extract automatically.

    Without an LLM client the tick logs once and skips: the extractor
    can't run identity-style on document bodies (it'd dump full doc
    text into the claim store and defeat the whole point).
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_claim_extract tick: no LLM client configured; "
            "extractor skipped this tick"
        )
        return

    sm = get_sessionmaker()
    workspace_ids = await _all_workspace_ids()
    total_claims = 0
    total_failed = 0
    for ws_id in workspace_ids:
        async with sm() as session:
            try:
                report = await extract_pending_workspace(
                    session, workspace_id=ws_id, llm_client=llm_client
                )
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception(
                    "knowledge_claim_extract tick: ws=%s failed", ws_id
                )
                total_failed += 1
                continue
        total_claims += report.claims_created
        total_failed += report.failed
    if total_claims or total_failed:
        log.info(
            "knowledge_claim_extract tick: workspaces=%d claims=%d failed=%d",
            len(workspace_ids),
            total_claims,
            total_failed,
        )


@cron_with_lock(
    lock=CronLockId.KNOWLEDGE_CLAIM_RECONCILE,
    name="knowledge_claim_reconcile",
)
async def _knowledge_claim_reconcile_tick() -> None:
    """Every 25 min: collapse near-duplicates and resolve supersedes.

    Runs offset 5 min after the extractor so freshly-inserted claims
    have time to land before the reconciler looks for neighbours.
    The reconciler tolerates a missing LLM client — the cosine
    fast-path (sim ≥ 0.95) still folds obvious duplicates, the
    LLM-judge band is just left alone.
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_claim_reconcile tick: no LLM client configured; "
            "running cosine-only reconciliation"
        )
        llm_client = None

    sm = get_sessionmaker()
    workspace_ids = await _all_workspace_ids()
    total_dup = total_refines = total_contradict = total_failed = 0
    for ws_id in workspace_ids:
        async with sm() as session:
            try:
                report = await reconcile_pending_workspace(
                    session, workspace_id=ws_id, llm_client=llm_client
                )
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception(
                    "knowledge_claim_reconcile tick: ws=%s failed", ws_id
                )
                total_failed += 1
                continue
        total_dup += report.auto_duplicates + report.llm_duplicates
        total_refines += report.refines
        total_contradict += report.contradicts
        total_failed += report.failed
    if total_dup or total_refines or total_contradict or total_failed:
        log.info(
            "knowledge_claim_reconcile tick: workspaces=%d duplicates=%d "
            "refines=%d contradicts=%d failed=%d",
            len(workspace_ids),
            total_dup,
            total_refines,
            total_contradict,
            total_failed,
        )


@cron_with_lock(
    lock=CronLockId.KNOWLEDGE_TOPIC_RENDER, name="knowledge_topic_render"
)
async def _knowledge_topic_render_tick() -> None:
    """Every 30 min: render derived topic-views from active claims.

    Per workspace, find topics with ≥3 active claims and render any
    whose claim_set_sha has drifted since the last render. Cache hits
    skip the LLM call entirely, so steady-state cost is dominated by
    the sha computation, not by tokens.

    Without an LLM client the renderer falls back to a deterministic
    bullet-list body; the next tick that does have a client refreshes
    the same row in place. We don't skip the tick on missing LLM —
    operators still get a structured fallback view they can search.
    """
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_topic_render tick: no LLM client; "
            "deterministic-only render this tick"
        )
        llm_client = None

    sm = get_sessionmaker()
    workspace_ids = await _all_workspace_ids()
    total_rendered = total_failed = 0
    for ws_id in workspace_ids:
        async with sm() as session:
            try:
                report = await render_workspace_topics(
                    session, workspace_id=ws_id, llm_client=llm_client
                )
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception(
                    "knowledge_topic_render tick: ws=%s failed", ws_id
                )
                total_failed += 1
                continue
        total_rendered += report.rendered
        total_failed += report.failed
    if total_rendered or total_failed:
        log.info(
            "knowledge_topic_render tick: workspaces=%d rendered=%d failed=%d",
            len(workspace_ids),
            total_rendered,
            total_failed,
        )


@cron_with_lock(
    lock=CronLockId.KNOWLEDGE_CLAIM_DECAY, name="knowledge_claim_decay"
)
async def _knowledge_claim_decay_tick() -> None:
    """Daily soft-stale of unconfirmed active claims.

    Per workspace: bulk-flip active claims whose ``last_seen_at``
    fell outside the decay window. The flip is non-destructive
    (``status='stale'``); operator review or a fresh source
    re-asserting the same text auto-revives the row through the
    extractor's dedup short-circuit.

    Excluded by construction:
    - ``superseded`` rows — already non-active via the supersedes
      graph; flipping them again would muddy the audit trail.
    - ``disputed`` rows — operator owes a decision; decay shouldn't
      sneak around the inbox review.

    Bulk UPDATE keeps the per-workspace cost O(1) round trips.
    Topic-view cache invalidation rides for free: any view whose
    active-claim set just lost a member computes a different
    ``claim_set_sha`` on the next renderer tick and regenerates.
    """
    sm = get_sessionmaker()
    workspace_ids = await _all_workspace_ids()
    total_flipped = 0
    for ws_id in workspace_ids:
        async with sm() as session:
            try:
                report = await decay_workspace_claims(
                    session, workspace_id=ws_id
                )
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception(
                    "knowledge_claim_decay tick: ws=%s failed", ws_id
                )
                continue
        total_flipped += report.flipped_stale
    if total_flipped:
        log.info(
            "knowledge_claim_decay tick: workspaces=%d flipped_stale=%d",
            len(workspace_ids),
            total_flipped,
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


@cron_with_lock(
    lock=CronLockId.LINEAR_TOKEN_REFRESH, name="linear_token_refresh"
)
async def _linear_token_refresh_tick() -> None:
    """Rotate every READY Linear install's access+refresh pair.

    Cadence-based: a credential whose ``last_rotated_at`` is older
    than ``LINEAR_TOKEN_REFRESH_HOURS`` (default 6h) gets swapped via
    Linear's ``grant_type=refresh_token`` flow. Operator's "connect
    Linear once" stays durable — the cron keeps a rolling-fresh access
    token in the credential row regardless of when the workspace is
    next hit by an agent run.
    """
    from backend.app.core.config import get_settings
    from backend.app.services.linear_token_refresh import (
        refresh_all_due_linear_tokens,
    )

    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            counts = await refresh_all_due_linear_tokens(
                session, settings=settings
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    if counts.get("rotated") or counts.get("refresh_failed"):
        log.info(
            "linear_token_refresh tick: %s",
            ", ".join(f"{k}={v}" for k, v in counts.items()),
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
    # External-source pull runs every 15 min so a connector that
    # advertises ``sync_interval_minutes=60`` actually re-syncs
    # within ~15 min of becoming due, instead of waiting for
    # the next operator visit to the sources page.
    register_cron(
        fn=_knowledge_sources_sync_tick,
        cron_expr="*/15 * * * *",
        job_id="knowledge_sources_sync",
    )
    # Claim extractor runs every 20 min — fast enough that an
    # operator who pushed a doc edit sees claims appear within minutes,
    # bounded enough that a full backfill of an existing workspace
    # paces itself without saturating the LLM API. The reconciler
    # offsets by +5 so freshly-inserted claims land before the
    # cosine-nearest sweep looks for neighbours.
    register_cron(
        fn=_knowledge_claim_extract_tick,
        cron_expr="*/20 * * * *",
        job_id="knowledge_claim_extract",
    )
    register_cron(
        fn=_knowledge_claim_reconcile_tick,
        cron_expr="5,25,45 * * * *",
        job_id="knowledge_claim_reconcile",
    )
    # Topic-view render runs every 30 min, offset by 10 from the
    # reconciler so a freshly-superseded claim flips out of any
    # affected view's claim_set_sha before the next render. Cache
    # hits make this cheap on workspaces with stable canon.
    register_cron(
        fn=_knowledge_topic_render_tick,
        cron_expr="15,45 * * * *",
        job_id="knowledge_topic_render",
    )
    # GC runs once daily. Cheap query (filtered DELETE by archived_at)
    # so an off-peak slot is fine; pick 03:15 UTC to avoid the on-the-
    # hour pile-ups from the harvest/route/synth chain.
    register_cron(
        fn=_knowledge_decay_tick,
        cron_expr="15 3 * * *",  # daily at 03:15 UTC
        job_id="knowledge_decay",
    )
    # Claim decay also runs once a day, offset 15 min from the
    # bucket-article GC so log lines don't interleave during the
    # 03:00-04:00 quiet window. The bulk-UPDATE shape keeps even
    # large-canon workspaces cheap; running daily is a deliberate
    # de-noise — operator-visible status flips shouldn't churn at
    # cron-tick cadence.
    register_cron(
        fn=_knowledge_claim_decay_tick,
        cron_expr="30 3 * * *",  # daily at 03:30 UTC
        job_id="knowledge_claim_decay",
    )
    # Linear token rotation runs every hour at :05; the per-install
    # check inside ``refresh_all_due_linear_tokens`` looks at
    # ``last_rotated_at`` against ``LINEAR_TOKEN_REFRESH_HOURS`` (default
    # 6h) so most ticks are fast no-ops and only every Nth tick actually
    # touches Linear. Cron expression at :05 keeps it out of the
    # knowledge-pipeline pile-ups around :15-:45.
    register_cron(
        fn=_linear_token_refresh_tick,
        cron_expr="5 * * * *",  # hourly at :05 (gated by per-install age)
        job_id="linear_token_refresh",
    )
    # E16/ELS-124 retired the Ship-side trigger-workflow scheduler.
    # The tracker poller + dispatcher (``apps/backend/app/services/
    # tracker_poller.py`` + ``dispatcher.py``) now drive every agent
    # run from Linear state transitions; no more fan-out cron over
    # every activated repo. ``_workflow_dispatch_tick`` and
    # ``workflow_dispatch_cron.py`` are deleted in the same commit.

    # E16/ELS-125 — workspace bundles (daily-digest / weekly-audit /
    # self-heal). The only time-driven dispatch path that survives.
    from backend.app.services.daily_scheduler import (
        register_all as register_workspace_ticks,
    )

    register_workspace_ticks()


__all__ = ["register_all"]
