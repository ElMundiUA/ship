"""Background worker entry point (arq).

Run as: ``arq backend.app.workers.main.WorkerSettings``.

The worker shares the same image, the same Settings, and the same database
as ``ship-server``. Concrete jobs (artifact repo sync, document parsing,
embedding refresh, daily/retro generation, OTLP export) land in dedicated
modules under :mod:`backend.app.workers` and are imported here so arq picks
them up at startup.
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from backend.app.core.config import get_settings
from backend.app.core.sentry import init_sentry
from backend.app.workers.git_sync import (
    cron_sync_pending_repos,
    settings_interval_minutes,
)
from backend.app.workers.secret_probe import cron_probe_pending_secrets


log = logging.getLogger("ship.worker")

# Worker boots with a different service tag so Sentry can split error rates
# between the API and background jobs without operators digging through tags.
init_sentry(service_name="ship-worker")


def _every_n_minutes(n: int) -> set[int]:
    """Return the minute-of-hour set arq's cron expects for "every N minutes"."""
    n = max(1, min(60, n))
    return set(range(0, 60, n))


async def heartbeat(ctx: dict) -> None:
    """Trivial heartbeat job used as a smoke-test until real jobs land.

    Runs every minute; emits a single info line so operators can see the
    worker is alive in container logs without standing up extra observability.
    """
    log.info("ship-worker heartbeat tick=%s", ctx.get("job_try"))


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """arq's discovery point for queue config + scheduled jobs."""

    redis_settings = _redis_settings()
    functions: list = []
    cron_jobs = [
        cron(heartbeat, minute=set(range(0, 60))),
        # Probe newly-saved or stale integration secrets on the half-minute so
        # `pending` rows turn green within ~30s of the user clicking "Save
        # secret". The job itself is bounded (32 rows, 6s/probe) so it can't
        # starve the heartbeat tick.
        cron(cron_probe_pending_secrets, minute=set(range(0, 60)), second={0, 30}),
        # Clone/fetch every registered remote artifact repo. Spacing is
        # operator-tunable via REPO_SYNC_INTERVAL_MINUTES (default 10) so a
        # large self-hosted install can throttle git traffic without forking.
        cron(
            cron_sync_pending_repos,
            minute=_every_n_minutes(settings_interval_minutes()),
        ),
    ]
    keep_result = 60
    job_timeout = 300
