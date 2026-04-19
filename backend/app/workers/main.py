"""Background worker entry point (arq).

Run as: ``arq backend.app.workers.main.WorkerSettings``.

The worker is opt-in (``docker compose --profile worker up``) and ships the
secret-probe cron for self-hosted operators who don't want the inline-on-save
behaviour the API also performs. Cloud SaaS deploys no worker container;
the API path covers everything.

The worker shares the same image, the same Settings, and the same database
as ``ship-server``.
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from backend.app.core.config import get_settings
from backend.app.core.sentry import init_sentry
from backend.app.workers.secret_probe import cron_probe_pending_secrets


log = logging.getLogger("ship.worker")

# Worker boots with a different service tag so Sentry can split error rates
# between the API and background jobs without operators digging through tags.
init_sentry(service_name="ship-worker")


async def heartbeat(ctx: dict) -> None:
    """Trivial heartbeat job used as a smoke-test until real jobs land.

    Runs every minute; emits a single info line so operators can see the
    worker is alive in container logs without standing up extra observability.
    """
    log.info("ship-worker heartbeat tick=%s", ctx.get("job_try"))


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    if not settings.redis_url:
        # Cloud SaaS topology no longer ships a worker container; arq invoked
        # against this entry point should exit cleanly so the orchestrator does
        # not flap. Local dev opts in via `docker compose --profile worker up`,
        # which sets REDIS_URL alongside the redis service.
        log.info(
            "REDIS_URL not configured; ship-worker is a no-op in this "
            "deployment, exiting 0"
        )
        raise SystemExit(0)
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """arq's discovery point for queue config + scheduled jobs."""

    redis_settings = _redis_settings()
    functions: list = []
    cron_jobs = [
        cron(heartbeat, minute=set(range(0, 60))),
        # Re-probe stale integration rows on the half-minute. The API path
        # already probes inline on save, so this is purely a guard for
        # rotated upstream tokens that operators left untouched. Bounded
        # (32 rows, 6s/probe) so it never starves the heartbeat tick.
        cron(cron_probe_pending_secrets, minute=set(range(0, 60)), second={0, 30}),
    ]
    keep_result = 60
    job_timeout = 300
