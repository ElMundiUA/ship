"""In-process scheduled jobs with Postgres-advisory-lock leader election.

KB-pipeline crons (and the legacy ARQ ones, eventually) live here.
The model is:

* Each FastAPI instance starts an :class:`AsyncIOScheduler` in its
  lifespan. Schedules are declared by calling :func:`register_cron`
  during startup.
* Before a job's body runs, the wrapper grabs a Postgres
  ``pg_try_advisory_lock(<id>)`` keyed by a globally-unique 64-bit
  integer from :class:`CronLockId`. If the lock is taken (another
  replica is already running the same tick), the wrapper exits
  silently. The lock auto-releases on disconnect, so a crashed or
  killed replica can't strand a job.
* When the body finishes (or raises), the wrapper releases the lock
  and lets APScheduler pick up the next firing.

Why advisory locks specifically:

* No table to keep, no extra round trips on the hot path
  (``pg_try_advisory_lock`` is sub-millisecond).
* Lock state is *connection-scoped*: dropping the connection
  releases the lock automatically. A k8s OOMKill or container
  restart doesn't leave a job stuck "running forever" — the next
  replica's tick succeeds at acquire.
* PG already covers leader election semantics; we don't reinvent
  the wheel with table-based row locks or a separate Redis.

This module exposes:

* :class:`CronLockId` — the registry of unique advisory-lock ids.
  Add a new entry **here** before adding a new cron, so collisions
  surface in PR review.
* :func:`cron_with_lock` — decorator that wraps a coroutine job with
  the acquire/release dance.
* :func:`register_cron` — convenience to register a job with the
  scheduler using a CronTrigger from a 5-field expression.
* :func:`get_scheduler` / :func:`start_scheduler` /
  :func:`stop_scheduler` — lifecycle hooks the FastAPI lifespan
  drives.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from backend.app.db.session import get_sessionmaker


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lock-id registry
# ---------------------------------------------------------------------------


class CronLockId(IntEnum):
    """Globally-unique 64-bit ids used as Postgres advisory-lock keys.

    Reserve a new id by **adding it here**, never by typing a number
    inline at the call site. Collisions silently make two unrelated
    jobs serialise behind the same lock — which would surface as
    sporadic "skipped this tick" log lines that take half a day to
    debug. Keeping the registry small + auditable is the cheapest
    way to make collisions impossible.

    Number range: positive ints below 2^31 keep the cast to BIGINT
    safe across Postgres advisory-lock variants. We start at 1001
    just to leave the low range to anything ad-hoc the operator
    might run from psql during incidents.
    """

    KNOWLEDGE_HARVEST = 1001
    KNOWLEDGE_ROUTE = 1002
    KNOWLEDGE_SYNTH = 1003
    KNOWLEDGE_DECAY = 1004
    KNOWLEDGE_SOURCES_SYNC = 1005
    KNOWLEDGE_CLAIM_EXTRACT = 1006
    KNOWLEDGE_CLAIM_RECONCILE = 1007
    KNOWLEDGE_TOPIC_RENDER = 1008
    KNOWLEDGE_CLAIM_DECAY = 1009
    LINEAR_TOKEN_REFRESH = 1010
    # Ship-side scheduler — fires the customer-side trigger workflow
    # via GitHub ``workflow_dispatch`` on a deterministic cadence.
    # Replaces the GHA ``schedule:`` cron in the starter workflow,
    # which GitHub heavily throttles under load (observed 4-8 hour
    # gaps between ticks in production). Backend-driven dispatch
    # gives operators a predictable "every N minutes" cycle.
    WORKFLOW_DISPATCH = 1011
    # E16 tracker poller (ELS-121) — diffs Linear ticket state every
    # ``SHIP_TRACKER_POLL_INTERVAL_S`` seconds and emits
    # ``tracker.event.received`` audit rows for the dispatcher.
    TRACKER_POLL = 1012
    # E16/ELS-125 workspace bundles — single-leader election across
    # replicas for the three time-driven workspace routines that
    # replaced the legacy reviewer chain.
    WORKSPACE_DAILY_DIGEST = 1013
    WORKSPACE_WEEKLY_AUDIT = 1014
    WORKSPACE_SELF_HEAL = 1015
    # FSM backstop scan (askslayer/PAC-32..36 2026-05-17). Event-
    # driven dispatch via tracker_poller fires on state changes; if
    # a maybe_dispatch was dropped (lock, refire-cap, transient
    # error), the ticket sits idle until something else moves its
    # state. This scan walks the Linear FSM filter per stage every
    # 15 min and re-fires any ticket whose last dispatch is older
    # than 20 min. Conservative cadence + idempotent dispatch (same
    # lock as the poller's path) makes the overlap safe.
    FSM_SCAN_BACKSTOP = 1016
    # Inbox stale-row dismiss (ELS-144) — rows with ``stale_after`` past
    # ``created_at + stale_after`` and ``status=new`` become dismissed.
    INBOX_STALE_SWEEP = 1017
    # Agent-dispatch lock sweeper (ELS-149) — releases dangling
    # ``project:*`` locks when the dispatched workflow_run finished
    # but never wrote ``agent_run.finish`` (crashed agent, GH dispatch
    # without workflow, etc.). Without this the 24h TTL blocks every
    # sibling ticket in the project for hours.
    AGENT_DISPATCH_LOCK_SWEEP = 1018
    # Inbox action_items backfill (ELS-165) — parses legacy
    # clarification rows' markdown bodies into structured
    # ``payload.action_items`` so the Decision UI renders pills.
    # Idempotent; only touches rows missing the structured payload.
    INBOX_ACTION_ITEMS_BACKFILL = 1019


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


JobFn = Callable[[], Awaitable[None]]


def cron_with_lock(
    *,
    lock: CronLockId,
    name: str | None = None,
) -> Callable[[JobFn], JobFn]:
    """Wrap an async job so only one replica runs each tick.

    The wrapped function is a no-arg coroutine; APScheduler calls it
    on the schedule registered via :func:`register_cron`. Inside the
    wrapper we open a fresh DB session (the lock binds to *that*
    connection — releasing the session releases the lock), try to
    acquire, and either run + release or skip silently.
    """

    def decorator(fn: JobFn) -> JobFn:
        job_label = name or fn.__name__

        async def wrapper() -> None:
            sm = get_sessionmaker()
            async with sm() as session:
                got = (
                    await session.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": int(lock)},
                    )
                ).scalar_one()
                if not got:
                    log.debug(
                        "cron %s skipped: another replica holds lock id=%d",
                        job_label,
                        int(lock),
                    )
                    return
                try:
                    await fn()
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("cron %s failed", job_label)
                    raise
                finally:
                    # Best-effort release. If the rollback above already
                    # closed the connection, the lock is gone anyway —
                    # but call unlock to keep the active path clean
                    # under sane conditions.
                    try:
                        await session.execute(
                            text("SELECT pg_advisory_unlock(:k)"),
                            {"k": int(lock)},
                        )
                    except Exception:  # pragma: no cover — defensive
                        pass

        wrapper.__name__ = fn.__name__
        wrapper.__qualname__ = fn.__qualname__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------


@dataclass
class _JobSpec:
    fn: JobFn
    cron_expr: str
    job_id: str


_SCHEDULER: AsyncIOScheduler | None = None
_REGISTERED: list[_JobSpec] = []


def register_cron(*, fn: JobFn, cron_expr: str, job_id: str) -> None:
    """Queue a job for registration.

    Called during module import (e.g. ``backend.app.services.cron_jobs``)
    so registrations are idempotent across reloads. Actual scheduler
    binding happens in :func:`start_scheduler`.
    """
    _REGISTERED.append(_JobSpec(fn=fn, cron_expr=cron_expr, job_id=job_id))


def get_scheduler() -> AsyncIOScheduler | None:
    return _SCHEDULER


def start_scheduler() -> AsyncIOScheduler:
    """Build the scheduler, attach every registered job, start ticking.

    Idempotent — safe to call once per FastAPI startup. Replicas all
    call this; advisory-lock contention on each tick keeps work
    single-leader.
    """
    global _SCHEDULER
    if _SCHEDULER is not None:
        return _SCHEDULER
    sched = AsyncIOScheduler(timezone="UTC")
    for spec in _REGISTERED:
        trigger = CronTrigger.from_crontab(spec.cron_expr, timezone="UTC")
        sched.add_job(
            spec.fn,
            trigger=trigger,
            id=spec.job_id,
            misfire_grace_time=120,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info(
            "cron registered: id=%s schedule=%s", spec.job_id, spec.cron_expr
        )
    sched.start()
    _SCHEDULER = sched
    return sched


async def stop_scheduler() -> None:
    """Shutdown hook for the FastAPI lifespan."""
    global _SCHEDULER
    if _SCHEDULER is None:
        return
    sched, _SCHEDULER = _SCHEDULER, None
    try:
        sched.shutdown(wait=False)
    except Exception:  # pragma: no cover — best-effort
        pass


__all__ = [
    "CronLockId",
    "cron_with_lock",
    "register_cron",
    "start_scheduler",
    "stop_scheduler",
    "get_scheduler",
]
