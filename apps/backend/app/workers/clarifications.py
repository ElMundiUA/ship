"""Periodic tracker → Ship clarifications projection (D13).

The agent writes ``@ship clarification:`` comments + ``ship:needs-
clarification`` labels on the customer's tracker. Ship's console
renders one inbox of open questions across repos / trackers / runs;
this cron keeps that inbox fresh without the console having to hit
the tracker APIs on every page view (which would fall over on rate
limits the moment a workspace has more than a handful of questions).

Cadence: every 5 minutes. The manual ``POST .../clarifications/sync``
route already covers "I need this now" for onboarding; the cron is the
steady-state safety net.

Budget: one DB session per tick; tracker fan-out per workspace is
bounded by :func:`~backend.app.services.clarifications_sync
.resolve_tracker_bindings` (one Linear call + one GitHub call per
activated repo). The projection logs individual tracker failures into
the per-workspace report rather than raising, so one flaky tenant
can't knock out the whole batch.
"""

from __future__ import annotations

import logging

from backend.app.core.config import get_settings
from backend.app.db.session import get_sessionmaker
from backend.app.services import clarifications_sync


log = logging.getLogger("ship.worker.clarifications")


async def cron_sync_tracker_clarifications(ctx: dict) -> None:
    """arq entry point — project every workspace's tracker state.

    ``ctx`` is arq's per-job context; we ignore it apart from logging
    the attempt counter so operators can spot retry storms in the
    worker logs.
    """
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            reports = await clarifications_sync.sync_all_workspaces(
                session, settings=settings
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception(
            "clarifications sync cron failed (try=%s)", ctx.get("job_try")
        )
        raise
    total_ingested = sum(r.ingested for r in reports)
    total_updated = sum(r.updated for r in reports)
    total_stale = sum(r.stale_marked for r in reports)
    log.info(
        "clarifications sync done: workspaces=%d ingested=%d updated=%d stale=%d",
        len(reports),
        total_ingested,
        total_updated,
        total_stale,
    )


__all__ = ["cron_sync_tracker_clarifications"]
