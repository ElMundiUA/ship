"""Fast-path cascade: route + synth one workspace right after a sync.

The four hourly knowledge ticks (harvest, route, synth, decay) are
batch-mode by design — they collect deltas across every workspace and
run one LLM-backed pass per stage so synthesis stays coherent (one draft
article per bucket per tick rather than N rampaging per-note drafts).

That batching is correct for steady-state operation but produces a
cold-start latency spike: an operator who just connected Notion or hit
*Sync now* has to wait up to 30 min for the next ``:30`` route tick and
then up to 10 min more for the ``:40`` synth tick before any draft
appears. The fast-path here closes that gap by re-running the route +
synth stages **for the affected workspace only**, immediately after a
sync writes fresh ``Improvement(kind='knowledge_note')`` rows.

Coordination model (matches :mod:`backend.app.services.cron`):

- Each stage tries the same global advisory lock the cron wrapper
  takes (``CronLockId.KNOWLEDGE_ROUTE`` / ``KNOWLEDGE_SYNTH``). If the
  lock is already held — the regular tick is mid-sweep on a different
  replica — we skip silently. The fresh notes will be picked up by
  the in-flight sweep, so nothing is lost.
- Each stage opens its own session: a stage failure rolls back only
  its own transaction and the next stage still runs.
- LLM client is best-effort. ``None`` is fine: route falls back to
  centroid-only, synth skips silently — same semantics as the cron
  wrapper.

The cascade is **idempotent**. A second pass over the same notes is a
no-op (route filters on ``routed_bucket_id IS NULL``; synth filters on
notes not yet linked to an article via ``BucketArticleSource``). That
property is what lets us also call it from the ``sync_due`` cron tick
without worrying about double-routing if the regular ``:30`` tick
overlaps.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_sessionmaker
from backend.app.services.agent.client import AgentClient, pick_default_client
from backend.app.services.cron import CronLockId
from backend.app.services.knowledge_router import route_pending_notes
from backend.app.services.knowledge_synth import synthesise_workspace


log = logging.getLogger(__name__)


@dataclass(slots=True)
class CascadeReport:
    """Outcome of one ``cascade_workspace_pipeline`` call.

    Used by tests + future telemetry — production callers fire the
    cascade and ignore the result (cron will catch up either way).
    """

    workspace_id: uuid.UUID
    route_ran: bool = False
    synth_ran: bool = False
    route_skipped_lock_held: bool = False
    synth_skipped_lock_held: bool = False
    errors: list[str] = field(default_factory=list)


async def cascade_workspace_pipeline(
    workspace_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    llm_client: AgentClient | None = None,
) -> CascadeReport:
    """Run route → synth for one workspace right after fresh notes land.

    Best-effort end-to-end. Each stage:

    1. Opens a fresh session.
    2. Tries the matching cron advisory lock. Skips silently if held.
    3. Runs the per-workspace pipeline call, commits.
    4. Releases the lock.

    A failure in any stage is logged + recorded in the report, but
    never raises — the source row's notes are already committed by
    the caller, so the regular cron tick can finish whatever the
    fast-path didn't.
    """
    settings = settings or get_settings()
    if llm_client is None:
        try:
            llm_client = pick_default_client(settings)
        except Exception:  # noqa: BLE001
            log.info(
                "cascade ws=%s: no LLM client configured (centroid-only "
                "routing, synth will skip)",
                workspace_id,
            )
            llm_client = None

    report = CascadeReport(workspace_id=workspace_id)

    await _run_stage(
        report=report,
        lock=CronLockId.KNOWLEDGE_ROUTE,
        stage="route",
        body=lambda session: route_pending_notes(
            session, workspace_id=workspace_id, llm_client=llm_client
        ),
    )

    await _run_stage(
        report=report,
        lock=CronLockId.KNOWLEDGE_SYNTH,
        stage="synth",
        body=lambda session: synthesise_workspace(
            session, workspace_id=workspace_id, llm_client=llm_client
        ),
    )

    return report


async def _run_stage(
    *,
    report: CascadeReport,
    lock: CronLockId,
    stage: str,
    body,
) -> None:
    """Run ``body(session)`` under the given advisory lock, best-effort.

    Lifted into its own helper because route and synth share the
    exact same lock-try-commit-release dance and the duplication
    obscured the actual stage call.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": int(lock)}
            )
        ).scalar_one()
        if not got:
            log.info(
                "cascade ws=%s: %s lock %d held — letting cron handle it",
                report.workspace_id,
                stage,
                int(lock),
            )
            setattr(report, f"{stage}_skipped_lock_held", True)
            return
        try:
            await body(session)
            await session.commit()
            setattr(report, f"{stage}_ran", True)
        except Exception as exc:  # noqa: BLE001 — never let cascade poison the request
            await session.rollback()
            log.exception(
                "cascade ws=%s: %s failed", report.workspace_id, stage
            )
            report.errors.append(f"{stage}: {exc}")
        finally:
            try:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": int(lock)}
                )
            except Exception:  # pragma: no cover — defensive
                pass


__all__ = ["CascadeReport", "cascade_workspace_pipeline"]
