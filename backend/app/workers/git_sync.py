"""Worker job: keep registered git artifact repos up to date.

Pairs with :mod:`backend.app.services.git_sync` — that module knows how to
clone/fetch a single :class:`ArtifactRepo`; this module decides which rows
to look at on each tick and writes the verdicts back.

Selection policy
----------------

A repo is eligible when:

- its ``url`` is **not** a ``file://`` path (remote clones only — file paths
  are read inline by the resolver), and
- it has either never synced (``last_sync_at IS NULL``) **or** the most
  recent sync is older than :data:`REPROBE_AFTER_SECONDS`.

We process up to :data:`MAX_PER_TICK` rows per tick, oldest-first, so a
freshly registered repo turns green within one cron interval and a single
flaky upstream can't starve the rest of the workspace.

The ``git`` calls themselves are blocking subprocesses, so we offload each
one with :func:`asyncio.to_thread` to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import ArtifactRepo
from backend.app.db.session import get_sessionmaker
from backend.app.services.git_sync import (
    SyncOutcome,
    apply_outcome,
    is_remote_url,
    sync_repo,
)


log = logging.getLogger("ship.worker.git_sync")

# Re-sync at the same cadence as the cron tick by default; surfaced as a
# constant for tests so they can monkeypatch it without touching settings.
REPROBE_AFTER_SECONDS = 10 * 60
MAX_PER_TICK = 8  # bounded fan-out — git clones are slow, don't oversubscribe


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def _sync_one(repo: ArtifactRepo) -> tuple[ArtifactRepo, SyncOutcome]:
    outcome = await asyncio.to_thread(sync_repo, repo)
    return repo, outcome


async def sync_pending_repos(*, max_rows: int = MAX_PER_TICK) -> dict[str, Any]:
    """Re-clone or fetch the next batch of remote artifact repos.

    Returns a small summary dict so the cron caller can log a line and so
    the test suite can assert on counts. Pure async; no Redis dependency.
    """
    cutoff = _now() - timedelta(seconds=REPROBE_AFTER_SECONDS)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        stmt = (
            select(ArtifactRepo)
            # The DB doesn't know what counts as "remote" so we filter in Python
            # below; oldest-first ordering keeps the queue fair.
            .where(
                or_(
                    ArtifactRepo.last_sync_at.is_(None),
                    ArtifactRepo.last_sync_at < cutoff,
                )
            )
            .order_by(ArtifactRepo.last_sync_at.asc().nullsfirst())
            .limit(max_rows * 4)  # over-fetch then drop file:// rows
        )
        rows = (await session.execute(stmt)).scalars().all()
        remotes = [r for r in rows if is_remote_url(r.url)][:max_rows]
        if not remotes:
            return {"checked": 0, "ok": 0, "error": 0}

        results = await asyncio.gather(*[_sync_one(r) for r in remotes])

        ok = err = 0
        for repo, outcome in results:
            apply_outcome(repo, outcome)
            if outcome.error is None:
                ok += 1
            else:
                err += 1
                log.warning(
                    "git sync failed for repo %s (%s): %s",
                    repo.id,
                    repo.url,
                    outcome.error,
                )
        await session.commit()
        return {"checked": len(remotes), "ok": ok, "error": err}


async def cron_sync_pending_repos(ctx: dict) -> dict[str, Any]:
    """arq entrypoint. Logs a one-line summary so docker logs stay readable."""
    summary = await sync_pending_repos()
    if summary["checked"]:
        log.info(
            "git_sync tick: checked=%d ok=%d error=%d",
            summary["checked"],
            summary["ok"],
            summary["error"],
        )
    return summary


def settings_interval_minutes() -> int:
    """Read the cron interval from settings, defaulting to 10 minutes."""
    return max(1, get_settings().repo_sync_interval_minutes)


__all__ = [
    "MAX_PER_TICK",
    "REPROBE_AFTER_SECONDS",
    "cron_sync_pending_repos",
    "settings_interval_minutes",
    "sync_pending_repos",
]
