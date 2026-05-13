"""Worker job: archive idle chat threads (Wave C).

Runs hourly (`:05` past the hour to avoid the on-the-hour stampede)
and flips ``status`` from ``active`` to ``archived`` on threads
whose ``last_user_activity_at`` is older than
:data:`~backend.app.services.agent.chat_threads.THRESHOLD_DAYS_DEFAULT`
days. The actual selection + mutation lives in
:mod:`backend.app.services.agent.chat_threads` so it can be tested
without spinning up arq or Redis.

Idempotent by construction — the SELECT clause only matches
``status='active'`` rows, so a second tick over the same window
finds nothing to do and writes nothing. ``archived_at`` therefore
captures the *first* time the sweeper saw the row idle, never a
later re-flip.

Bounded: at most :data:`~backend.app.services.agent.chat_threads
.BATCH_LIMIT` rows per tick, ordered ``last_user_activity_at`` ASC,
so a backlog drains oldest-first across multiple ticks instead of
flooding any single transaction.

Sentry: spans are emitted automatically by ``ArqIntegration``
(see :func:`backend.app.core.sentry.init_sentry`); we deliberately
do not wrap the body in a manual ``Sentry.start_span`` to stay
consistent with the rest of the worker module.
"""

from __future__ import annotations

import logging

from backend.app.db.session import get_sessionmaker
from backend.app.services.agent.chat_threads import (
    archive_idle_chat_threads_once,
)


log = logging.getLogger("ship.worker.archive_chat_threads")


async def archive_idle_chat_threads(ctx: dict) -> dict[str, int]:
    """arq entrypoint — archive one batch of idle chat threads.

    ``ctx`` is arq's per-job context; we only read ``job_try`` for
    log breadcrumbs so retry storms are visible in container logs.
    Returns a small dict so arq's ``keep_result`` window lets
    operators eyeball outcomes without tailing logs.
    """
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            result = await archive_idle_chat_threads_once(session)
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception(
            "archive_idle_chat_threads cron failed (try=%s)",
            ctx.get("job_try"),
        )
        raise

    summary = result.as_dict()
    # Structured one-liner so docker logs stay readable and operators
    # can grep for the cron name directly. ``extra=`` matches the
    # idiom we use elsewhere in the worker package.
    log.info(
        "archive_idle_chat_threads",
        extra={
            "archived": summary["archived"],
            "skipped": summary["skipped"],
            "scanned": summary["scanned"],
        },
    )
    # arq surfaces this dict in its job result log; ``skipped`` is
    # included because zero today is a useful baseline if we ever
    # start filtering rows out at the Python layer (workspace flag,
    # etc.) and want to spot a regression at a glance.
    return summary


__all__ = ["archive_idle_chat_threads"]
