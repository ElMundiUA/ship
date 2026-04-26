"""Per-repo home rollup (RFC-0008 §F — PR-4 "Now/Trends").

``/r/<owner>/<repo>`` used to dump the whole workspace dashboard under
every repo, which (a) double-counted activity whenever the workspace
had more than one active repo and (b) buried repo-level signal under
cross-repo totals. This endpoint is the per-repo inverse: one request
returns both the "what's happening *right now* on this repo" tiles and
the trend histogram the second tab renders.

Shape is intentionally monolithic so the Console renders a single
snapshot (both tabs consume the same payload, no polling second
endpoint when the user flips Now ↔ Trends). Everything is a live
aggregation over ``PipelineRun`` / ``WorkflowRun`` / ``AgentRequest``
plus the ``WorkspaceRepo`` / ``GitHubInstallation`` health flags —
zero denorm, the cost stays tiny per repo.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import (
    AgentRequest,
    Pipeline,
    PipelineRun,
    WorkflowRun,
)
from backend.app.db.session import get_session
from backend.app.services.seed_bundle import BUNDLE_VERSION


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}",
    tags=["repo-home"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


ActivityKind = Literal["pipeline", "workflow", "agent"]
ActivityStatus = Literal["running", "succeeded", "failed", "cancelled", "other"]


class RepoHomeRecentActivity(BaseModel):
    """A single entry in the ``now.recent_activity`` tail. The three
    source tables get normalised into one list ordered by ``at``
    descending so the UI renders a unified "what happened last" feed
    without having to sort three lists client-side."""

    kind: ActivityKind
    status: ActivityStatus
    title: str
    at: str
    html_url: str | None = None


class RepoHomeLaneBreakdown(BaseModel):
    """Per-lane activity summary inside the window. The lane_id is
    whatever ``PipelineRun.lane_id`` was populated with (falls back to
    ``pipeline.lane_id``); legacy rows without a lane_id bucket under
    ``(unnamed)`` so they remain visible."""

    lane_id: str
    runs: int
    successes: int
    failures: int
    last_run_at: str | None


class RepoHomeNow(BaseModel):
    runs_in_flight: int
    runs_last_24h: int
    successes_last_24h: int
    failures_last_24h: int
    last_run_at: str | None
    last_success_at: str | None
    dispatches_in_flight: int
    lanes_enabled: int
    lanes_total: int
    bundle_installed_version: str | None
    bundle_current_version: str
    bundle_drift: bool
    install_suspended: bool
    install_missing: bool
    recent_activity: list[RepoHomeRecentActivity] = Field(default_factory=list)


class RepoHomeTrendBucket(BaseModel):
    """One day in the trends histogram. ``other`` catches anything
    neither a clean success nor a clean failure (cancelled, running
    at query time, dispatched-but-not-reported, …). The UI stacks
    success / failure / other so operators see the full shape and
    not just "did something run"."""

    day: str  # YYYY-MM-DD, UTC calendar day
    total: int
    successes: int
    failures: int
    other: int


class RepoHomeTrendTotals(BaseModel):
    runs: int
    successes: int
    failures: int
    other: int
    success_rate: float | None


class RepoHomeTrends(BaseModel):
    window_days: int
    buckets: list[RepoHomeTrendBucket]
    totals: RepoHomeTrendTotals
    lanes: list[RepoHomeLaneBreakdown]


class RepoHomeReport(BaseModel):
    workspace_id: uuid.UUID
    repo_id: uuid.UUID
    full_name: str
    generated_at: str
    window_days: int
    now: RepoHomeNow
    trends: RepoHomeTrends


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/home", response_model=RepoHomeReport)
async def get_repo_home(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    window_days: int = Query(default=30, ge=1, le=180),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoHomeReport:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")

    suspended_at: datetime | None = None
    if repo.installation_id is not None:
        suspended_at = (
            await session.execute(
                select(GitHubInstallation.suspended_at).where(
                    GitHubInstallation.id == repo.installation_id
                )
            )
        ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)
    day_24h = now - timedelta(hours=24)

    # Lanes: count of ``Pipeline`` rows for the repo. We split
    # ``enabled`` out so the UI can show "3 of 5 lanes wired".
    lanes_total, lanes_enabled = await _count_lanes(session, repo_id)

    # --- Pull raw rows once, aggregate in Python. Three separate
    # tables, each small for a single repo over 30 days; batching
    # here is cheaper than three grouped queries because the "now"
    # tiles need row-level detail (recent activity feed) anyway.
    pipeline_runs = await _fetch_pipeline_runs(
        session, workspace_id, repo_id, window_start
    )
    workflow_runs = await _fetch_workflow_runs(
        session, workspace_id, repo_id, window_start
    )
    agent_requests = await _fetch_agent_requests(
        session, workspace_id, repo_id, window_start
    )

    activity: list[_Activity] = []
    activity.extend(_pipeline_activity(pipeline_runs))
    activity.extend(_workflow_activity(workflow_runs))
    activity.extend(_agent_activity(agent_requests))

    now_tile = _build_now(
        repo=repo,
        suspended_at=suspended_at,
        activity=activity,
        day_24h=day_24h,
        now=now,
        lanes_total=lanes_total,
        lanes_enabled=lanes_enabled,
    )
    trends = _build_trends(activity, window_days=window_days, now=now)

    return RepoHomeReport(
        workspace_id=workspace_id,
        repo_id=repo_id,
        full_name=repo.full_name,
        generated_at=now.isoformat(),
        window_days=window_days,
        now=now_tile,
        trends=trends,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Activity:
    """Normalised in-memory view over a run from any of the three
    source tables. Unified shape keeps the aggregators (``now`` +
    ``trends``) simple — they both iterate the same list."""

    kind: ActivityKind
    status: ActivityStatus
    title: str
    at: datetime
    html_url: str | None
    lane_id: str


async def _count_lanes(
    session: AsyncSession, repo_id: uuid.UUID
) -> tuple[int, int]:
    total = (
        await session.execute(
            select(func.count()).where(Pipeline.repo_id == repo_id)
        )
    ).scalar_one() or 0
    enabled = (
        await session.execute(
            select(func.count()).where(
                Pipeline.repo_id == repo_id, Pipeline.enabled.is_(True)
            )
        )
    ).scalar_one() or 0
    return int(total), int(enabled)


async def _fetch_pipeline_runs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    window_start: datetime,
) -> list[tuple[PipelineRun, Pipeline]]:
    rows = (
        await session.execute(
            select(PipelineRun, Pipeline)
            .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
            .where(
                PipelineRun.workspace_id == workspace_id,
                Pipeline.repo_id == repo_id,
                # Either started in the window OR still open (in-flight
                # runs can be older than window_start when the runner
                # wedges — we still want them in the "now" tile).
                (PipelineRun.started_at >= window_start)
                | (PipelineRun.status.in_(("running", "queued", "dispatched"))),
            )
            .order_by(desc(PipelineRun.started_at))
        )
    ).all()
    return [(r, p) for r, p in rows]


async def _fetch_workflow_runs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    window_start: datetime,
) -> list[WorkflowRun]:
    rows = (
        await session.execute(
            select(WorkflowRun)
            .where(
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.repo_id == repo_id,
                (WorkflowRun.created_at >= window_start)
                | (WorkflowRun.status.in_(("queued", "in_progress"))),
            )
            .order_by(desc(WorkflowRun.created_at))
        )
    ).scalars().all()
    return list(rows)


async def _fetch_agent_requests(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    window_start: datetime,
) -> list[AgentRequest]:
    rows = (
        await session.execute(
            select(AgentRequest)
            .where(
                AgentRequest.workspace_id == workspace_id,
                AgentRequest.repo_id == repo_id,
                (AgentRequest.created_at >= window_start)
                | (
                    AgentRequest.status.in_(
                        ("dispatched", "running", "queued")
                    )
                ),
            )
            .order_by(desc(AgentRequest.created_at))
        )
    ).scalars().all()
    return list(rows)


def _pipeline_activity(
    rows: list[tuple[PipelineRun, Pipeline]],
) -> list[_Activity]:
    out: list[_Activity] = []
    for run, pipeline in rows:
        at = run.started_at or run.created_at
        status = _normalise_run_status(run.status)
        # ``run.lane_id`` is a UUID FK to the RFC-0007 ``Lane`` table;
        # ``pipeline.lane_id`` is the legacy string key. Operators care
        # about the human-facing label, so fall back to the string key
        # whenever we don't have a joined Lane row.
        lane = (
            str(run.lane_id) if run.lane_id is not None else pipeline.lane_id
        ) or "(unnamed)"
        title = pipeline.name or pipeline.workflow_id or lane
        out.append(
            _Activity(
                kind="pipeline",
                status=status,
                title=title,
                at=at,
                html_url=None,
                lane_id=str(lane),
            )
        )
    return out


def _workflow_activity(rows: list[WorkflowRun]) -> list[_Activity]:
    out: list[_Activity] = []
    for row in rows:
        at = row.created_at
        status = _normalise_conclusion(row.status, row.conclusion)
        title = row.name or row.event or "workflow run"
        out.append(
            _Activity(
                kind="workflow",
                status=status,
                title=title,
                at=at,
                html_url=row.html_url,
                # GitHub-originated workflow runs are not guaranteed to
                # map to a Ship lane. Bucket them under a synthetic
                # "github" lane so trends can still surface noise.
                lane_id="(github)",
            )
        )
    return out


def _agent_activity(rows: list[AgentRequest]) -> list[_Activity]:
    out: list[_Activity] = []
    for row in rows:
        at = row.created_at
        status = _normalise_run_status(row.status)
        title = row.pattern_id or row.agent_slug
        out.append(
            _Activity(
                kind="agent",
                status=status,
                title=title,
                at=at,
                html_url=row.gh_html_url,
                lane_id=row.pattern_id or "(ad-hoc)",
            )
        )
    return out


def _normalise_run_status(raw: str) -> ActivityStatus:
    match raw:
        case "succeeded" | "success":
            return "succeeded"
        case "failed" | "failure" | "errored" | "rejected":
            return "failed"
        case "running" | "queued" | "dispatched" | "dispatching":
            return "running"
        case "cancelled" | "canceled" | "cancel_requested":
            return "cancelled"
        case _:
            return "other"


def _normalise_conclusion(
    status: str, conclusion: str | None
) -> ActivityStatus:
    # GitHub's shape: a run is ``in_progress`` / ``queued`` /
    # ``completed``; conclusion fills only on completion.
    if status in ("queued", "in_progress"):
        return "running"
    if conclusion is None:
        return "other"
    match conclusion:
        case "success":
            return "succeeded"
        case "failure" | "timed_out" | "startup_failure":
            return "failed"
        case "cancelled":
            return "cancelled"
        case _:
            return "other"


_RECENT_ACTIVITY_LIMIT = 10


def _build_now(
    *,
    repo: WorkspaceRepo,
    suspended_at: datetime | None,
    activity: list[_Activity],
    day_24h: datetime,
    now: datetime,
    lanes_total: int,
    lanes_enabled: int,
) -> RepoHomeNow:
    runs_in_flight = sum(1 for a in activity if a.status == "running")
    # Dispatches-in-flight is the agent-request subset; operators read
    # it as "how many one-shot requests are still waiting for a
    # callback" which is a distinct urgency signal from scheduled
    # lanes being mid-run.
    dispatches_in_flight = sum(
        1 for a in activity if a.kind == "agent" and a.status == "running"
    )

    in_24h = [a for a in activity if a.at >= day_24h]
    runs_last_24h = len(in_24h)
    successes_last_24h = sum(1 for a in in_24h if a.status == "succeeded")
    failures_last_24h = sum(1 for a in in_24h if a.status == "failed")

    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    for a in activity:
        if last_run_at is None or a.at > last_run_at:
            last_run_at = a.at
        if a.status == "succeeded" and (
            last_success_at is None or a.at > last_success_at
        ):
            last_success_at = a.at

    recent = sorted(activity, key=lambda a: a.at, reverse=True)[
        :_RECENT_ACTIVITY_LIMIT
    ]

    bundle_drift = _bundle_version_lt(repo.installed_bundle_version, BUNDLE_VERSION)

    return RepoHomeNow(
        runs_in_flight=runs_in_flight,
        runs_last_24h=runs_last_24h,
        successes_last_24h=successes_last_24h,
        failures_last_24h=failures_last_24h,
        last_run_at=last_run_at.isoformat() if last_run_at else None,
        last_success_at=(
            last_success_at.isoformat() if last_success_at else None
        ),
        dispatches_in_flight=dispatches_in_flight,
        lanes_enabled=lanes_enabled,
        lanes_total=lanes_total,
        bundle_installed_version=repo.installed_bundle_version,
        bundle_current_version=BUNDLE_VERSION,
        bundle_drift=bundle_drift,
        install_suspended=suspended_at is not None,
        install_missing=repo.installation_id is None,
        recent_activity=[
            RepoHomeRecentActivity(
                kind=a.kind,
                status=a.status,
                title=a.title,
                at=a.at.isoformat(),
                html_url=a.html_url,
            )
            for a in recent
        ],
    )


def _bundle_version_lt(installed: str | None, current: str) -> bool:
    if installed is None:
        return False
    return _bundle_version_tuple(installed) < _bundle_version_tuple(current)


def _bundle_version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(value).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _build_trends(
    activity: list[_Activity], *, window_days: int, now: datetime
) -> RepoHomeTrends:
    today = now.astimezone(timezone.utc).date()
    window_start_date = today - timedelta(days=window_days - 1)

    # Always emit a bucket per day so the UI renders a fixed-width
    # histogram with explicit zeros instead of a ragged line the eye
    # has to re-align.
    buckets: dict[date, RepoHomeTrendBucket] = {}
    for i in range(window_days):
        d = window_start_date + timedelta(days=i)
        buckets[d] = RepoHomeTrendBucket(
            day=d.isoformat(), total=0, successes=0, failures=0, other=0
        )

    lane_stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "runs": 0,
            "successes": 0,
            "failures": 0,
            "last_run_at": None,
        }
    )

    tot_runs = tot_ok = tot_fail = tot_other = 0

    for a in activity:
        a_date = a.at.astimezone(timezone.utc).date()
        if a_date < window_start_date or a_date > today:
            # Fetched-but-out-of-window row (e.g. long-running).
            # Keep it out of the histogram so "30d" really means 30d.
            continue
        b = buckets[a_date]
        b.total += 1
        if a.status == "succeeded":
            b.successes += 1
            tot_ok += 1
        elif a.status == "failed":
            b.failures += 1
            tot_fail += 1
        else:
            b.other += 1
            tot_other += 1
        tot_runs += 1

        ls = lane_stats[a.lane_id]
        ls["runs"] = int(ls["runs"]) + 1  # type: ignore[call-overload]
        if a.status == "succeeded":
            ls["successes"] = int(ls["successes"]) + 1  # type: ignore[call-overload]
        elif a.status == "failed":
            ls["failures"] = int(ls["failures"]) + 1  # type: ignore[call-overload]
        prev = ls["last_run_at"]
        if prev is None or a.at > prev:  # type: ignore[operator]
            ls["last_run_at"] = a.at

    success_rate: float | None = None
    if tot_runs > 0:
        success_rate = round(tot_ok / tot_runs, 4)

    lanes: list[RepoHomeLaneBreakdown] = []
    for lane_id, s in lane_stats.items():
        last = s["last_run_at"]
        lanes.append(
            RepoHomeLaneBreakdown(
                lane_id=lane_id,
                runs=int(s["runs"]),  # type: ignore[arg-type]
                successes=int(s["successes"]),  # type: ignore[arg-type]
                failures=int(s["failures"]),  # type: ignore[arg-type]
                last_run_at=(
                    last.isoformat() if isinstance(last, datetime) else None
                ),
            )
        )
    # Busiest lanes first; tie-break alphabetically so snapshots are
    # deterministic.
    lanes.sort(key=lambda l: (-l.runs, l.lane_id))

    return RepoHomeTrends(
        window_days=window_days,
        buckets=[buckets[d] for d in sorted(buckets.keys())],
        totals=RepoHomeTrendTotals(
            runs=tot_runs,
            successes=tot_ok,
            failures=tot_fail,
            other=tot_other,
            success_rate=success_rate,
        ),
        lanes=lanes,
    )


__all__ = ["router"]
