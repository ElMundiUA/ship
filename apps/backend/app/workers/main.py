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
import uuid

from arq import cron
from arq.connections import RedisSettings

from backend.app.core.config import get_settings
from backend.app.core.sentry import init_sentry
from backend.app.db.session import get_sessionmaker
from backend.app.services.repo_intel import harvest_repo_intel
from backend.app.workers.archive_chat_threads import archive_idle_chat_threads
from backend.app.workers.clarifications import cron_sync_tracker_clarifications
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


async def harvest_repo_intel_job(
    ctx: dict,
    workspace_id: str,
    repo_id: str,
    triggered_by: str = "wizard",
) -> dict:
    """arq entry point for the per-repo intel harvest (P5-03).

    Opens its own session so the job is independent of any request
    scope. Returns a small dict with the result so arq's
    ``keep_result`` window lets operators eyeball what landed without
    digging through logs.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        report = await harvest_repo_intel(
            session=session,
            workspace_id=uuid.UUID(workspace_id),
            repo_id=uuid.UUID(repo_id),
            triggered_by=triggered_by,
        )
        await session.commit()
    log.info(
        "repo_intel harvest done repo=%s version=%d duration_ms=%d",
        repo_id,
        report.version,
        report.duration_ms,
    )
    return {
        "intel_id": str(report.intel_id),
        "version": report.version,
        "duration_ms": report.duration_ms,
        "files_examined": report.files_examined,
        "languages_detected": report.languages_detected,
        "knowledge_articles_written": report.knowledge_articles_written,
    }


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
    functions: list = [harvest_repo_intel_job]
    cron_jobs = [
        cron(heartbeat, minute=set(range(0, 60))),
        # Re-probe stale integration rows on the half-minute. The API path
        # already probes inline on save, so this is purely a guard for
        # rotated upstream tokens that operators left untouched. Bounded
        # (32 rows, 6s/probe) so it never starves the heartbeat tick.
        cron(cron_probe_pending_secrets, minute=set(range(0, 60)), second={0, 30}),
        # D13 — project tracker-labelled clarifications into Ship's
        # inbox every 5 minutes. The admin-triggered sync route covers
        # onboarding; this is the steady-state safety net so questions
        # the agent raised show up in the console without operators
        # clicking "sync" manually.
        cron(
            cron_sync_tracker_clarifications,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
        # Wave C — sweep idle chat threads into the archived view once
        # an hour. Fires at ``:05`` so we don't pile onto the on-the-
        # hour cadence the other crons share. Bounded to 500 rows
        # per tick (see :data:`backend.app.services.agent.chat_threads
        # .BATCH_LIMIT`); a backlog drains across multiple ticks.
        cron(archive_idle_chat_threads, minute={5}),
        # NB: knowledge_harvest moved to APScheduler + Postgres advisory
        # locks (services/cron.py + cron_jobs.py) — runs in-process
        # inside the FastAPI container, no Redis needed. The legacy
        # ARQ entry point (workers/knowledge_harvest.py) is kept around
        # for the rollback knob until the next cleanup PR removes it.
    ]
    keep_result = 60
    job_timeout = 300
