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
from backend.app.services.knowledge_harvest import harvest_all_workspaces


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


__all__ = ["register_all"]
