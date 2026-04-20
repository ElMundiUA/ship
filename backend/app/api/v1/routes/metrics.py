"""SHIP-book metrics dashboard (D11).

One endpoint — ``GET /v1/workspaces/{ws}/metrics/overview?window=30d``
— that returns every aggregate the ``/metrics`` page renders. Kept
flat on purpose: each "panel" on the UI maps to one top-level key in
the response, so adding / removing a panel is a single-site change.

**What's here (pilot scope):**

- :attr:`Overview.pipelines` — lane counts + enabled ratio,
  per-kind breakdown.
- :attr:`Overview.runs` — :class:`PipelineRun` status distribution,
  success rate, per-kind + per-trigger breakdowns, mean wall-clock
  duration for terminal runs.
- :attr:`Overview.clarifications` — C9 queue stats (open/answered/
  skipped/stale, answer rate, median time-to-answer in hours).
- :attr:`Overview.improvements` — C8 decision distribution + accept
  rate.
- :attr:`Overview.chat` — C10 thread counts (active/resolved/
  archived), message total, thread→ticket rate.
- :attr:`Overview.dora` — DORA approximations from our cached
  :class:`PullRequest` + :class:`WorkflowRun` rows. Flagged as
  *approximations* because:
    - "deploy frequency" = merged PRs / window days (we don't track
      deploys directly yet; the pilot's definition of a "deploy" is
      a merge to ``default_branch``);
    - "change failure rate" = failed :class:`WorkflowRun` / total;
    - MTTR is **not computed** — it needs failure→recovery linking
      we don't have yet. Returned as ``null`` and the UI labels it
      "coming soon" rather than pretend-zero.

**What's deliberately missing:**

- No per-day time series. Pilot volumes (< 1k runs / workspace)
  make the endpoint cheap as an aggregate, but rendering a time
  series requires another round-trip + a charting lib we haven't
  added. Added when we need it.
- No per-user metrics. Workspace-wide only. Per-user rollups land
  with the leaderboard feature (see backlog scratch).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_surface import (
    ChatMessage,
    ChatThread,
    Clarification,
    Improvement,
)
from backend.app.db.models.pipelines import (
    Pipeline,
    PipelineRun,
    PullRequest,
    WorkflowRun,
)
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/metrics", tags=["metrics"]
)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


WINDOWS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class KindCount(BaseModel):
    kind: str
    total: int
    succeeded: int = 0
    failed: int = 0
    success_rate: float | None = None


class Bucket(BaseModel):
    key: str
    value: int


class PipelinesPanel(BaseModel):
    total: int
    enabled: int
    disabled: int
    by_kind: list[KindCount]


class RunsPanel(BaseModel):
    total: int
    succeeded: int
    failed: int
    running: int
    other: int
    success_rate: float | None
    avg_duration_seconds: float | None
    by_kind: list[KindCount]
    by_trigger: list[Bucket]


class ClarificationsPanel(BaseModel):
    total: int
    open: int
    answered: int
    skipped: int
    stale: int
    answer_rate: float | None
    median_resolution_hours: float | None


class ImprovementsPanel(BaseModel):
    total: int
    pending: int
    accepted: int
    declined: int
    deferred: int
    accept_rate: float | None


class ChatPanel(BaseModel):
    threads_total: int
    threads_active: int
    threads_resolved: int
    threads_archived: int
    messages_total: int
    ticket_rate: float | None


class DoraPanel(BaseModel):
    prs_opened: int
    prs_merged: int
    deploy_frequency_per_day: float | None = Field(
        None,
        description=(
            "Approximation: merged PRs / window days. Uses cached "
            "PullRequest rows — webhook drift applies."
        ),
    )
    avg_lead_time_hours: float | None = Field(
        None,
        description="Mean (opened_at → merged_at) for merged PRs in window.",
    )
    workflow_runs_total: int
    workflow_runs_failed: int
    change_failure_rate: float | None = Field(
        None,
        description="failed WorkflowRun / total in window.",
    )
    mttr_hours: float | None = Field(
        None,
        description=(
            "Not computed in pilot — needs failure→recovery linking "
            "that doesn't exist yet. Always null."
        ),
    )


class Overview(BaseModel):
    window_days: int
    window_start: datetime
    window_end: datetime
    pipelines: PipelinesPanel
    runs: RunsPanel
    clarifications: ClarificationsPanel
    improvements: ImprovementsPanel
    chat: ChatPanel
    dora: DoraPanel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    """Percentage as 0..1 float, or ``None`` when the denominator is 0."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


@dataclass(frozen=True)
class _Window:
    days: int
    start: datetime
    end: datetime


def _resolve_window(label: str) -> _Window:
    if label not in WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown window '{label}'. Expected one of: "
                f"{', '.join(sorted(WINDOWS))}."
            ),
        )
    days = WINDOWS[label]
    end = datetime.now(timezone.utc)
    return _Window(days=days, start=end - timedelta(days=days), end=end)


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------


async def _pipelines_panel(
    session: AsyncSession, workspace_id: uuid.UUID
) -> PipelinesPanel:
    """All-time snapshot of lane configuration.

    Not windowed — the UI pairs this with the windowed ``runs``
    panel so you can tell "3 of 4 lanes are enabled, and they ran N
    times in the last 30d". Windowing config would give us nothing
    and would make the card wrong immediately after a repo activates.
    """
    rows = (
        await session.execute(
            select(Pipeline.kind, Pipeline.enabled).where(
                Pipeline.workspace_id == workspace_id
            )
        )
    ).all()
    total = len(rows)
    enabled = sum(1 for _k, e in rows if e)
    by_kind_buckets: dict[str, dict[str, int]] = {}
    for kind, e in rows:
        bucket = by_kind_buckets.setdefault(kind, {"total": 0, "enabled": 0})
        bucket["total"] += 1
        if e:
            bucket["enabled"] += 1
    by_kind = [
        KindCount(kind=k, total=v["total"], succeeded=v["enabled"])
        for k, v in sorted(by_kind_buckets.items())
    ]
    return PipelinesPanel(
        total=total,
        enabled=enabled,
        disabled=total - enabled,
        by_kind=by_kind,
    )


async def _runs_panel(
    session: AsyncSession, workspace_id: uuid.UUID, window: _Window
) -> RunsPanel:
    """Windowed run telemetry with per-kind + per-trigger breakdowns."""
    base = select(
        PipelineRun.status,
        PipelineRun.trigger,
        PipelineRun.started_at,
        PipelineRun.finished_at,
        Pipeline.kind,
    ).join(Pipeline, Pipeline.id == PipelineRun.pipeline_id).where(
        PipelineRun.workspace_id == workspace_id,
        PipelineRun.created_at >= window.start,
    )
    rows = (await session.execute(base)).all()

    total = len(rows)
    succeeded = sum(1 for r in rows if r.status == "succeeded")
    failed = sum(1 for r in rows if r.status == "failed")
    running = sum(1 for r in rows if r.status == "running")
    other = total - succeeded - failed - running

    # Avg duration across terminal runs with both timestamps present.
    durations = [
        (r.finished_at - r.started_at).total_seconds()
        for r in rows
        if r.started_at and r.finished_at
    ]
    avg_duration = (
        round(sum(durations) / len(durations), 2) if durations else None
    )

    by_kind_raw: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = by_kind_raw.setdefault(
            r.kind, {"total": 0, "succeeded": 0, "failed": 0}
        )
        bucket["total"] += 1
        if r.status == "succeeded":
            bucket["succeeded"] += 1
        elif r.status == "failed":
            bucket["failed"] += 1
    by_kind = [
        KindCount(
            kind=k,
            total=v["total"],
            succeeded=v["succeeded"],
            failed=v["failed"],
            success_rate=_safe_ratio(
                v["succeeded"], v["succeeded"] + v["failed"]
            ),
        )
        for k, v in sorted(by_kind_raw.items())
    ]

    by_trigger_raw: dict[str, int] = {}
    for r in rows:
        by_trigger_raw[r.trigger] = by_trigger_raw.get(r.trigger, 0) + 1
    by_trigger = [
        Bucket(key=k, value=v)
        for k, v in sorted(by_trigger_raw.items())
    ]

    return RunsPanel(
        total=total,
        succeeded=succeeded,
        failed=failed,
        running=running,
        other=other,
        success_rate=_safe_ratio(succeeded, succeeded + failed),
        avg_duration_seconds=avg_duration,
        by_kind=by_kind,
        by_trigger=by_trigger,
    )


async def _clarifications_panel(
    session: AsyncSession, workspace_id: uuid.UUID, window: _Window
) -> ClarificationsPanel:
    """Windowed C9 stats + median resolution time.

    Median (not mean) on purpose — a single stale row answered a
    month late skews the mean hard; median keeps the card honest for
    the "what's the usual turnaround" question.
    """
    rows = (
        await session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id,
                Clarification.created_at >= window.start,
            )
        )
    ).scalars().all()

    by_status = {"open": 0, "answered": 0, "skipped": 0, "stale": 0}
    resolution_hours: list[float] = []
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.status == "answered" and row.answered_at:
            delta = row.answered_at - row.created_at
            resolution_hours.append(delta.total_seconds() / 3600.0)

    median: float | None = None
    if resolution_hours:
        sorted_h = sorted(resolution_hours)
        mid = len(sorted_h) // 2
        if len(sorted_h) % 2:
            median = round(sorted_h[mid], 2)
        else:
            median = round((sorted_h[mid - 1] + sorted_h[mid]) / 2, 2)

    total = len(rows)
    return ClarificationsPanel(
        total=total,
        open=by_status["open"],
        answered=by_status["answered"],
        skipped=by_status["skipped"],
        stale=by_status["stale"],
        answer_rate=_safe_ratio(
            by_status["answered"], by_status["answered"] + by_status["skipped"]
        ),
        median_resolution_hours=median,
    )


async def _improvements_panel(
    session: AsyncSession, workspace_id: uuid.UUID, window: _Window
) -> ImprovementsPanel:
    rows = (
        await session.execute(
            select(Improvement.decision).where(
                Improvement.workspace_id == workspace_id,
                Improvement.created_at >= window.start,
            )
        )
    ).all()
    by_decision = {"pending": 0, "accepted": 0, "declined": 0, "deferred": 0}
    for (decision,) in rows:
        by_decision[decision] = by_decision.get(decision, 0) + 1
    decided = (
        by_decision["accepted"] + by_decision["declined"] + by_decision["deferred"]
    )
    return ImprovementsPanel(
        total=len(rows),
        pending=by_decision["pending"],
        accepted=by_decision["accepted"],
        declined=by_decision["declined"],
        deferred=by_decision["deferred"],
        accept_rate=_safe_ratio(by_decision["accepted"], decided),
    )


async def _chat_panel(
    session: AsyncSession, workspace_id: uuid.UUID, window: _Window
) -> ChatPanel:
    threads = (
        await session.execute(
            select(ChatThread.id, ChatThread.status).where(
                ChatThread.workspace_id == workspace_id,
                ChatThread.created_at >= window.start,
            )
        )
    ).all()
    by_status = {"active": 0, "resolved": 0, "archived": 0}
    thread_ids: list[uuid.UUID] = []
    for thread_id, st in threads:
        by_status[st] = by_status.get(st, 0) + 1
        thread_ids.append(thread_id)

    messages_total = 0
    if thread_ids:
        messages_total = (
            await session.execute(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.thread_id.in_({*thread_ids})
                )
            )
        ).scalar_one()
    terminal = by_status["resolved"] + by_status["archived"]
    return ChatPanel(
        threads_total=len(threads),
        threads_active=by_status["active"],
        threads_resolved=by_status["resolved"],
        threads_archived=by_status["archived"],
        messages_total=messages_total,
        ticket_rate=_safe_ratio(by_status["resolved"], terminal),
    )


async def _dora_panel(
    session: AsyncSession, workspace_id: uuid.UUID, window: _Window
) -> DoraPanel:
    prs = (
        await session.execute(
            select(
                PullRequest.merged,
                PullRequest.opened_at,
                PullRequest.merged_at,
            ).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.opened_at >= window.start,
            )
        )
    ).all()
    prs_opened = len(prs)
    prs_merged = sum(1 for p in prs if p.merged)
    # Lead time only defined for merged PRs with both timestamps.
    lead_times = [
        (p.merged_at - p.opened_at).total_seconds() / 3600.0
        for p in prs
        if p.merged and p.merged_at and p.opened_at
    ]
    avg_lead = (
        round(sum(lead_times) / len(lead_times), 2) if lead_times else None
    )
    deploy_freq = (
        round(prs_merged / window.days, 3) if window.days > 0 else None
    )

    wfs = (
        await session.execute(
            select(WorkflowRun.conclusion).where(
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.started_at >= window.start,
            )
        )
    ).all()
    wf_total = len(wfs)
    wf_failed = sum(
        1 for (c,) in wfs if c in {"failure", "timed_out", "action_required"}
    )
    return DoraPanel(
        prs_opened=prs_opened,
        prs_merged=prs_merged,
        deploy_frequency_per_day=deploy_freq,
        avg_lead_time_hours=avg_lead,
        workflow_runs_total=wf_total,
        workflow_runs_failed=wf_failed,
        change_failure_rate=_safe_ratio(wf_failed, wf_total),
        mttr_hours=None,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=Overview)
async def metrics_overview(
    workspace_id: uuid.UUID,
    window: Literal["7d", "30d", "90d"] = Query("30d"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Overview:
    """Aggregate every D11 panel for a workspace in one round-trip."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    w = _resolve_window(window)
    return Overview(
        window_days=w.days,
        window_start=w.start,
        window_end=w.end,
        pipelines=await _pipelines_panel(session, workspace_id),
        runs=await _runs_panel(session, workspace_id, w),
        clarifications=await _clarifications_panel(session, workspace_id, w),
        improvements=await _improvements_panel(session, workspace_id, w),
        chat=await _chat_panel(session, workspace_id, w),
        dora=await _dora_panel(session, workspace_id, w),
    )


__all__ = ["router"]
