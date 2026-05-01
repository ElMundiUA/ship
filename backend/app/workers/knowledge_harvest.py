"""ARQ cron entry: hourly knowledge-note harvest.

Phase 1a of the knowledge-ingestion pipeline. Walks every workspace
once an hour and writes one ``Improvement`` row (kind='knowledge_note')
per resolved clarification that hasn't been harvested yet. Phase 1b
adds the LLM extractor plus chat-pack and inbox-comment sources;
Phase 2 routes each row to a workspace bucket; Phase 3 synthesises
draft articles.

The job opens its own session, does the harvest, commits, and logs
a one-line summary per workspace plus a totals row. Failures inside
one workspace are caught + logged so a single bad row can't starve
the others.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.app.core.config import get_settings
from backend.app.db.session import get_sessionmaker
from backend.app.services.agent.client import pick_default_client
from backend.app.services.knowledge_harvest import harvest_all_workspaces


log = logging.getLogger(__name__)


async def cron_harvest_knowledge_notes(ctx: dict[str, Any]) -> dict[str, int]:
    """Hourly entry point wired into :class:`WorkerSettings.cron_jobs`.

    Returns a small summary so arq surfaces non-zero rows in its
    job-result log without us having to grep stderr.
    """
    sm = get_sessionmaker()
    total_inspected = 0
    total_created = 0
    total_skipped_dup = 0
    total_errors = 0

    # Best-effort LLM client. If neither OPENAI nor ANTHROPIC keys are
    # configured for this deploy, the harvester transparently runs the
    # identity extractor on every source and never makes a model call.
    try:
        llm_client = pick_default_client(get_settings())
    except Exception:
        log.info(
            "knowledge_harvest: no LLM client configured; identity extractor only"
        )
        llm_client = None

    async with sm() as session:
        try:
            reports = await harvest_all_workspaces(session, llm_client=llm_client)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    for r in reports:
        total_inspected += r.inspected
        total_created += r.created
        total_skipped_dup += r.skipped_duplicate
        if r.errors:
            total_errors += 1
        if r.created or r.errors:
            log.info(
                "knowledge_harvest workspace=%s inspected=%d created=%d "
                "skipped_dup=%d skipped_no_answer=%d errors=%d",
                r.workspace_id,
                r.inspected,
                r.created,
                r.skipped_duplicate,
                r.skipped_no_answer,
                len(r.errors),
            )

    log.info(
        "knowledge_harvest tick total: workspaces=%d inspected=%d "
        "created=%d skipped_dup=%d errors=%d",
        len(reports),
        total_inspected,
        total_created,
        total_skipped_dup,
        total_errors,
    )

    return {
        "workspaces": len(reports),
        "inspected": total_inspected,
        "created": total_created,
        "skipped_duplicate": total_skipped_dup,
        "errors": total_errors,
    }
