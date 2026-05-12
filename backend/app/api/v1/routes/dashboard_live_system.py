"""Workspace "Live System" aggregator (Dashboard v2 — PR-2).

One denormalised endpoint feeds the middle column of the home
dashboard so the Console doesn't fan out to half a dozen separate
routes (knowledge runs, routine state, daily-digest queue, specialist
health) on every render.

Five blocks, all best-effort: a missing data source returns ``None``
or a zero-count and the UI renders ``—`` rather than failing the
whole panel.

- ``masthead`` — 7-day automation health: success rate (succeeded /
  finished), failure count, when we last ran, was the last run OK.
  Computed off ``pipeline_runs.finished_at >= now() - 7d``.
- ``knowledge`` — most recent ``KnowledgeIngestionRun`` + count of
  runs done in the last 24h. ``state_label`` is ``running`` if the
  most recent row is in progress, ``errored`` if it crashed,
  otherwise ``idle``.
- ``routines`` — top three enabled non-daily :class:`Routine` rows by
  ``last_run_at`` desc. The middle column renders the top two; we
  return three so the UI can shuffle without a second round-trip.
- ``daily`` — denormalised state of the daily-digest routine
  (``lane_id == 'daily'``) plus inbox queue counts.
- ``specialists`` — count of distinct FSM specialists in three
  buckets (idle / working / errored) derived from the most recent
  ``PipelineRun`` per ``Pipeline.lane_id`` mapped through
  :func:`_specialist_for_lane`. ``working_name`` is set only when
  exactly one specialist is currently running so the UI can render
  the targeted "X working" line; multi-runner state collapses to a
  generic count.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.processes import _specialist_for_lane
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import KnowledgeIngestionRun
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.lanes import Routine
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/dashboard/live-system",
    tags=["dashboard-live-system"],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class MastheadOut(BaseModel):
    """7-day system-health glyphs rendered in the block kicker."""

    success_rate_7d: float | None
    failures_7d: int
    last_run_at: datetime | None
    last_run_status: Literal["ok", "error"] | None


class KnowledgeOut(BaseModel):
    last_run_at: datetime | None
    last_status: Literal["pending", "running", "done", "error"] | None
    ingested_today: int
    state_label: Literal["idle", "running", "errored"]


class RoutineOut(BaseModel):
    name: str
    last_run_at: datetime | None
    last_run_status: str | None


class DailyOut(BaseModel):
    last_sent_at: datetime | None
    last_run_status: str | None
    queue_wins: int
    queue_blockers: int


class SpecialistsOut(BaseModel):
    """Workspace-level specialist health rollup.

    Buckets are mutually exclusive: every distinct specialist counted
    once. ``errored_names`` lists the specialist slugs whose most
    recent finished run was a failure — empty when health is clean.
    ``working_name`` is the slug of the only currently-running
    specialist (or ``None`` when zero or more-than-one are running);
    the UI uses it to render ``Specialists · code-writer working ·
    5 idle`` instead of a generic count.
    """

    idle_count: int
    working_count: int
    errored_count: int
    errored_names: list[str]
    working_name: str | None


class LiveSystemOut(BaseModel):
    masthead: MastheadOut
    knowledge: KnowledgeOut
    routines: list[RoutineOut]
    daily: DailyOut
    specialists: SpecialistsOut


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_RUN_OK_STATUSES = frozenset({"succeeded", "success", "ok", "done"})
_RUN_FAIL_STATUSES = frozenset({"failed", "error", "cancelled", "canceled"})
_RUN_ACTIVE_STATUSES = frozenset({"running", "queued", "pending"})

# Display labels for the canonical seven routine ids. The earlier
# shared constant in ``lane_recipes`` was retired with the rest of
# the per-recipe metadata (see lane_recipes.py module docstring).
_ROUTINE_LABELS: dict[str, str] = {
    "daily": "Daily",
    "retro": "Retro",
    "healthcheck": "Self-heal",
    "tech_review": "Tech review",
    "qa_review": "QA review",
    "security_review": "Security review",
    "process_review": "Process review",
}


# ---------------------------------------------------------------------------
# Block computers
# ---------------------------------------------------------------------------


async def _masthead(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> MastheadOut:
    cutoff = now - timedelta(days=7)
    rows = (
        await session.execute(
            select(PipelineRun.status, PipelineRun.finished_at)
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.finished_at.is_not(None),
                PipelineRun.finished_at >= cutoff,
            )
            .order_by(desc(PipelineRun.finished_at))
        )
    ).all()

    succeeded = 0
    failed = 0
    for status, _ in rows:
        if status in _RUN_OK_STATUSES:
            succeeded += 1
        elif status in _RUN_FAIL_STATUSES:
            failed += 1
    total_finished = succeeded + failed
    success_rate: float | None = (
        succeeded / total_finished if total_finished > 0 else None
    )

    last_run_at: datetime | None = None
    last_run_status: Literal["ok", "error"] | None = None
    if rows:
        latest_status, latest_finished = rows[0]
        last_run_at = latest_finished
        if latest_status in _RUN_OK_STATUSES:
            last_run_status = "ok"
        elif latest_status in _RUN_FAIL_STATUSES:
            last_run_status = "error"

    return MastheadOut(
        success_rate_7d=success_rate,
        failures_7d=failed,
        last_run_at=last_run_at,
        last_run_status=last_run_status,
    )


async def _knowledge(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> KnowledgeOut:
    last = (
        await session.execute(
            select(KnowledgeIngestionRun)
            .where(KnowledgeIngestionRun.workspace_id == workspace_id)
            .order_by(
                desc(
                    func.coalesce(
                        KnowledgeIngestionRun.finished_at,
                        KnowledgeIngestionRun.created_at,
                    )
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    cutoff_24h = now - timedelta(hours=24)
    ingested_today = (
        await session.execute(
            select(func.count(KnowledgeIngestionRun.id)).where(
                KnowledgeIngestionRun.workspace_id == workspace_id,
                KnowledgeIngestionRun.status == "done",
                KnowledgeIngestionRun.finished_at >= cutoff_24h,
            )
        )
    ).scalar_one()

    last_run_at: datetime | None = None
    last_status: Literal["pending", "running", "done", "error"] | None = None
    state_label: Literal["idle", "running", "errored"] = "idle"
    if last is not None:
        last_run_at = last.finished_at or last.created_at
        # Cast through Literal-ish: the model carries free-text but the
        # CHECK constraint pins values to the four below.
        if last.status in ("pending", "running", "done", "error"):
            last_status = last.status  # type: ignore[assignment]
        if last.status == "running":
            state_label = "running"
        elif last.status == "error":
            state_label = "errored"

    return KnowledgeOut(
        last_run_at=last_run_at,
        last_status=last_status,
        ingested_today=int(ingested_today or 0),
        state_label=state_label,
    )


async def _routines(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[list[RoutineOut], Routine | None]:
    """Top non-daily routines + the daily-routine row (or None).

    Returned tuple keeps both lookups in one query: ``daily_row`` is
    used by :func:`_daily` to pull last-sent state, the rest feed the
    middle column's routine strip.
    """
    rows = (
        await session.execute(
            select(Routine)
            .where(
                Routine.workspace_id == workspace_id,
                Routine.enabled.is_(True),
            )
            .order_by(
                # Most-recently-run lanes float to the top — that's the
                # signal "what did the bot just do?". NULLs (never run)
                # sort last via FILTER trick: replace NULL with epoch.
                desc(
                    func.coalesce(
                        Routine.last_run_at,
                        func.to_timestamp(0),
                    )
                )
            )
        )
    ).scalars().all()

    daily_row: Routine | None = None
    out: list[RoutineOut] = []
    for r in rows:
        if r.lane_id == "daily" and daily_row is None:
            daily_row = r
            continue
        if len(out) >= 3:
            continue
        out.append(
            RoutineOut(
                name=_ROUTINE_LABELS.get(
                    r.lane_id, r.lane_id.replace("_", " ").title()
                ),
                last_run_at=r.last_run_at,
                last_run_status=r.last_run_status,
            )
        )
    return out, daily_row


async def _daily(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    daily_row: Routine | None,
) -> DailyOut:
    queue_wins = (
        await session.execute(
            select(func.count(InboxItem.id)).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status == "new",
                InboxItem.type == "improvement",
            )
        )
    ).scalar_one()
    queue_blockers = (
        await session.execute(
            select(func.count(InboxItem.id)).where(
                InboxItem.workspace_id == workspace_id,
                InboxItem.status == "new",
                InboxItem.type.in_(("failure", "exception")),
            )
        )
    ).scalar_one()

    return DailyOut(
        last_sent_at=daily_row.last_run_at if daily_row else None,
        last_run_status=daily_row.last_run_status if daily_row else None,
        queue_wins=int(queue_wins or 0),
        queue_blockers=int(queue_blockers or 0),
    )


async def _specialists(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> SpecialistsOut:
    """Aggregate specialist health from the most recent run per pipeline.

    For each :class:`Pipeline` row in the workspace we look at the most
    recent :class:`PipelineRun` (both finished and in-progress). The
    pipeline's ``lane_id`` is mapped through :func:`_specialist_for_lane`
    to the canonical specialist; we then dedupe on the specialist
    slug and bucket it as working / errored / idle.
    """
    pipelines = (
        await session.execute(
            select(Pipeline.id, Pipeline.lane_id).where(
                Pipeline.workspace_id == workspace_id,
                Pipeline.enabled.is_(True),
            )
        )
    ).all()

    if not pipelines:
        return SpecialistsOut(
            idle_count=0,
            working_count=0,
            errored_count=0,
            errored_names=[],
            working_name=None,
        )

    pipeline_ids = [pid for pid, _ in pipelines]
    lane_by_pipeline: dict[uuid.UUID, str] = {pid: lane for pid, lane in pipelines}

    # Window the run lookup to last 24h so a year-old failure doesn't
    # stick a specialist in the errored bucket forever. We order by
    # ``finished_at`` first so still-running rows (NULL finished_at)
    # don't accidentally outrank a just-finished one — coalesce
    # NULL → started_at → created_at so every row sorts.
    cutoff = now - timedelta(hours=24)
    activity_at = func.coalesce(
        PipelineRun.finished_at,
        PipelineRun.started_at,
        PipelineRun.created_at,
    )
    runs = (
        await session.execute(
            select(
                PipelineRun.pipeline_id,
                PipelineRun.status,
            )
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.pipeline_id.in_(pipeline_ids),
                PipelineRun.created_at >= cutoff,
            )
            .order_by(desc(activity_at))
        )
    ).all()

    # Latest status per pipeline within the window.
    latest_per_pipeline: dict[uuid.UUID, str] = {}
    for pid, status in runs:
        if pid in latest_per_pipeline:
            continue
        latest_per_pipeline[pid] = status

    # Specialist → worst-class status across its pipelines.
    # Priority order: working > errored > idle. A specialist whose
    # pipelines include both "running" and "failed" reads as
    # "working" — it's actively trying again, not stuck.
    by_specialist: dict[str, str] = {}
    for pid, lane_id in lane_by_pipeline.items():
        specialist = _specialist_for_lane(lane_id)
        latest = latest_per_pipeline.get(pid)
        if latest in _RUN_ACTIVE_STATUSES:
            by_specialist[specialist] = "working"
        elif latest in _RUN_FAIL_STATUSES:
            if by_specialist.get(specialist) != "working":
                by_specialist[specialist] = "errored"
        else:
            # No recent run, or it succeeded — idle is the default.
            by_specialist.setdefault(specialist, "idle")

    working = [s for s, st in by_specialist.items() if st == "working"]
    errored = sorted(s for s, st in by_specialist.items() if st == "errored")
    idle = [s for s, st in by_specialist.items() if st == "idle"]

    return SpecialistsOut(
        idle_count=len(idle),
        working_count=len(working),
        errored_count=len(errored),
        errored_names=errored,
        working_name=working[0] if len(working) == 1 else None,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("", response_model=LiveSystemOut)
async def get_live_system(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> LiveSystemOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    now = datetime.now(timezone.utc)
    masthead = await _masthead(session, workspace_id, now)
    knowledge = await _knowledge(session, workspace_id, now)
    routines, daily_row = await _routines(session, workspace_id)
    daily = await _daily(session, workspace_id, daily_row)
    specialists = await _specialists(session, workspace_id, now)
    return LiveSystemOut(
        masthead=masthead,
        knowledge=knowledge,
        routines=routines,
        daily=daily,
        specialists=specialists,
    )


__all__ = ["router"]
