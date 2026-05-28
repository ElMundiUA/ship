"""Workspace-level scheduler — daily-digest / weekly-audit / self-heal.

The only place where time-driven cron survives after the E16
cutover (ELS-124). Ticket-bound work fires via tracker_poller +
dispatcher events; the routines here have no ticket trigger and
need a clock to wake them.

Each registered tick walks every workspace with an enabled
integration and calls ``dispatcher.maybe_dispatch_workspace_bundle``
for the corresponding bundle. The dispatcher applies the same lock
+ cap semantics it uses for ticket dispatches, just on a separate
counter (``WORKSPACE_BUNDLE_CAP=1``) so daily/weekly/self-heal
don't compete with SDLC dispatches for the workspace's 4-slot
ticket cap.

Cadence (all UTC, override via env):

  daily-digest   ``0 9 * * *``    — 09:00 daily (PO morning inbox)
  weekly-audit   ``0 9 * * MON``  — 09:00 Monday  (week kick-off)
  self-heal      ``*/60 * * * *`` — once per hour (small-and-often)

Per ELS-125 the bundles consolidate the legacy reviewer routines
(daily-retro, learning-capture, tech / qa / security / process
reviewers) into 3 bundle prompts, ~70% token saving on workspace
audit cycles.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from backend.app.db.models.integrations import (
    NativeIntegrationInstallation,
)
from backend.app.db.session import get_sessionmaker
from backend.app.services.cron import CronLockId, cron_with_lock, register_cron
from backend.app.services.dispatcher import (
    maybe_dispatch_workspace_bundle,
)


log = logging.getLogger(__name__)


async def _dispatch_bundle_for_every_workspace(
    *,
    bundle_id: str,
    trigger_kind: str,
) -> None:
    """Fire ``bundle_id`` for every workspace that has a Linear
    integration (the only tracker the dispatcher can resolve today).

    Per-workspace failures stay isolated — one tenant's bad install
    can't starve the rest of the tick. The dispatcher itself emits
    ``agent_run.dispatch`` audit rows on success and structured
    refusal rows on every failure path, so this function only needs
    to log iteration progress.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        installs = (
            (
                await session.execute(
                    select(NativeIntegrationInstallation).where(
                        NativeIntegrationInstallation.provider == "linear",
                        NativeIntegrationInstallation.status == "ready",
                    )
                )
            )
            .scalars()
            .all()
        )

        fired = 0
        refused = 0
        for install in installs:
            try:
                result = await maybe_dispatch_workspace_bundle(
                    session,
                    workspace_id=install.workspace_id,
                    bundle_id=bundle_id,
                    trigger_kind=trigger_kind,
                )
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()
                log.exception(
                    "%s tick: workspace %s dispatch raised",
                    bundle_id,
                    install.workspace_id,
                )
                continue
            if result.fired:
                fired += 1
            else:
                refused += 1

        log.info(
            "%s tick: workspaces=%d fired=%d refused=%d",
            bundle_id,
            len(installs),
            fired,
            refused,
        )


# ---------------------------------------------------------------------------
# Tick coroutines — wrapped by ``cron_with_lock`` for single-leader election
# ---------------------------------------------------------------------------


@cron_with_lock(lock=CronLockId.WORKSPACE_DAILY_DIGEST, name="daily_digest")
async def _daily_digest_tick() -> None:
    await _dispatch_bundle_for_every_workspace(
        bundle_id="daily-digest", trigger_kind="daily_tick"
    )


@cron_with_lock(lock=CronLockId.WORKSPACE_WEEKLY_AUDIT, name="weekly_audit")
async def _weekly_audit_tick() -> None:
    await _dispatch_bundle_for_every_workspace(
        bundle_id="weekly-audit", trigger_kind="weekly_tick"
    )


# self_heal hourly workspace bundle DELETED 2026-05-28 (Phase 2 of
# the event-driven rearchitecture). The bundle was the largest cron-
# clock source by event volume — fanning out one dispatch per
# workspace every top-of-hour, regardless of whether anything in the
# workspace had changed. With the Phase 1 label-driven freeze in
# place, stuck tickets opt themselves out; with daily-digest +
# weekly-audit still firing, operators still get the rollup view.
# Hourly self-heal was pure cron-spam against tickets the dispatcher
# already knows about.


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all() -> None:
    """Register the workspace digest ticks against the cron scheduler.

    Called from ``cron_jobs.register_all`` during FastAPI lifespan.
    Cron expressions are hard-coded for now; if operators need to
    tune them per deploy they move into ``Settings``.
    """
    register_cron(
        fn=_daily_digest_tick,
        cron_expr="0 9 * * *",  # 09:00 UTC daily
        job_id="daily_digest",
    )
    register_cron(
        fn=_weekly_audit_tick,
        cron_expr="0 9 * * 1",  # 09:00 UTC Monday
        job_id="weekly_audit",
    )


__all__ = ["register_all"]
