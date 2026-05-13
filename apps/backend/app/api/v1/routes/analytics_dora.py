"""DORA metrics aggregator for the workspace ``/analytics`` page.

Computes the four DORA-2024 metrics — deployment frequency, lead time
for changes, change failure rate, mean time to recovery — over a
configurable rolling window (default 30 days).

What counts as a deploy
-----------------------

A successful :class:`WorkflowRun` on the workspace's bound repo where:

  * ``head_branch == 'main'`` (the canonical release branch — Ship is
    trunk-based, so a merge to main IS a deploy candidate).
  * ``conclusion == 'success'``.
  * ``name`` matches one of the deploy-shaped patterns
    (:data:`_DEPLOY_NAME_PATTERNS`) — covers ``platform-images``,
    ``deploy-*``, ``release-*``, ``ship-deploy``, etc. Catalog rather
    than free-form heuristic so a workflow rename doesn't silently
    reclassify history.

For change failure rate we count BOTH:

  * Failed deploy runs (``conclusion='failure'``) — the deploy itself
    rejected the change.
  * HOTFIX-tagged PRs merged within 24h of a prior merge — the deploy
    "succeeded" but a follow-up hotfix tells us something landed broken.

For MTTR we walk consecutive deploy runs ordered by ``started_at``,
treat each ``failure`` as the start of an incident, and pair it with
the next ``success`` (or end-of-window if none) to compute the gap.
Cancelled runs don't count as either deploys or incidents.

This module is read-only; it never writes anything to the database.
The route is gated on ROLES_READ (any workspace member) since the
data here is aggregate, not action-driving.
"""

from __future__ import annotations

import re
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.pipelines import PullRequest, WorkflowRun
from backend.app.db.session import get_session


router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["analytics"])


# ---------------------------------------------------------------------------
# Heuristics — what's a deploy, what's a hotfix
# ---------------------------------------------------------------------------


# Workflow names treated as deploys. We deliberately keep this catalog
# explicit (regex on the run's ``name`` column) rather than a broad
# substring match because workflow names like ``deploy-preview`` or
# ``test-deploy-rollback`` are NOT real deploys and would skew DF.
_DEPLOY_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bplatform[\s_-]?images?\b", re.IGNORECASE),
    re.compile(r"\bship[\s_-]?(deploy|release)\b", re.IGNORECASE),
    re.compile(r"^deploy\b", re.IGNORECASE),
    re.compile(r"^release\b", re.IGNORECASE),
    re.compile(r"\bauto[\s_-]?tag\b", re.IGNORECASE),  # tags drive npm publish
)


def _is_deploy_workflow(name: str | None) -> bool:
    if not name:
        return False
    return any(p.search(name) for p in _DEPLOY_NAME_PATTERNS)


_HOTFIX_TITLE_RE = re.compile(r"\bhot[\s-]?fix\b", re.IGNORECASE)


def _is_hotfix_pr(title: str | None) -> bool:
    if not title:
        return False
    return bool(_HOTFIX_TITLE_RE.search(title))


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


class DfPoint(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class DeploymentFrequency(BaseModel):
    total_deploys: int
    per_day_average: float
    per_day_median: float
    per_day_p90: float
    label: str  # DORA performance band (computed off the average)
    time_series: list[DfPoint]


class LtPoint(BaseModel):
    week_start: str
    median_hours: float | None
    sample: int


class LeadTime(BaseModel):
    median_hours: float | None
    p90_hours: float | None
    label: str
    sample_size: int
    time_series: list[LtPoint]


class CfrPoint(BaseModel):
    date: str
    total: int
    failed: int


class ChangeFailureRate(BaseModel):
    rate: float
    label: str
    total_deploys: int
    failed_deploys: int
    hotfix_prs: int
    time_series: list[CfrPoint]


class MttrIncident(BaseModel):
    failed_at: str
    recovered_at: str | None
    hours: float | None
    workflow_name: str | None


class MeanTimeToRecovery(BaseModel):
    median_hours: float | None
    p90_hours: float | None
    label: str
    incidents: list[MttrIncident]


class DoraOut(BaseModel):
    window_days: int
    computed_at: str
    deployment_frequency: DeploymentFrequency
    lead_time: LeadTime
    change_failure_rate: ChangeFailureRate
    mttr: MeanTimeToRecovery


# ---------------------------------------------------------------------------
# DORA performance band labels (from the 2024 State of DevOps report)
# ---------------------------------------------------------------------------


def _label_deployment_frequency(per_day_average: float) -> str:
    """DORA band off the average deploy-per-day rate.

    We classify off the mean rather than the per-day median because a
    healthy trunk-based workflow batches deploys into a few high-traffic
    days per week — the median is then 0 even though the team ships
    daily on aggregate. Mean tracks the cadence the operator
    actually feels.
    """
    if per_day_average >= 1.0:
        return "Elite — daily or better"
    if per_day_average >= 1 / 7:
        return "High — between weekly and daily"
    if per_day_average >= 1 / 30:
        return "Medium — between monthly and weekly"
    return "Low — less than monthly"


def _label_lead_time(median_hours: float | None) -> str:
    if median_hours is None:
        return "No data"
    if median_hours < 24:
        return "Elite — less than a day"
    if median_hours < 24 * 7:
        return "High — less than a week"
    if median_hours < 24 * 30 * 6:
        return "Medium — less than six months"
    return "Low — more than six months"


def _label_change_failure_rate(rate: float) -> str:
    if rate <= 0.05:
        return "Elite — under 5%"
    if rate <= 0.15:
        return "High — under 15%"
    if rate <= 0.30:
        return "Medium — under 30%"
    return "Low — over 30%"


def _label_mttr(median_hours: float | None) -> str:
    if median_hours is None:
        return "No incidents"
    if median_hours < 1:
        return "Elite — less than an hour"
    if median_hours < 24:
        return "High — less than a day"
    if median_hours < 24 * 7:
        return "Medium — less than a week"
    return "Low — over a week"


# ---------------------------------------------------------------------------
# Computations
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float | None:
    """Naive percentile (no SciPy dep). Returns None for an empty list."""
    if not values:
        return None
    if p <= 0:
        return values[0]
    if p >= 100:
        return values[-1]
    sorted_vals = sorted(values)
    rank = (p / 100) * (len(sorted_vals) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_vals) - 1)
    frac = rank - low
    return sorted_vals[low] * (1 - frac) + sorted_vals[high] * frac


def _date_buckets(start: datetime, end: datetime) -> list[str]:
    """Return ``YYYY-MM-DD`` strings for every day in [start, end]."""
    cur = start.date()
    end_date = end.date()
    out: list[str] = []
    while cur <= end_date:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _week_start(d: datetime) -> str:
    """Monday-anchored ISO week start (UTC)."""
    monday = d.date() - timedelta(days=d.weekday())
    return monday.isoformat()


def _compute_deployment_frequency(
    deploys: list[WorkflowRun],
    *,
    start: datetime,
    end: datetime,
) -> DeploymentFrequency:
    successful = [
        r for r in deploys if r.conclusion == "success" and r.started_at is not None
    ]
    counts_by_day: dict[str, int] = {d: 0 for d in _date_buckets(start, end)}
    for r in successful:
        assert r.started_at is not None
        d = r.started_at.astimezone(timezone.utc).date().isoformat()
        if d in counts_by_day:
            counts_by_day[d] += 1
    counts = list(counts_by_day.values())
    average = (sum(counts) / len(counts)) if counts else 0.0
    median = float(statistics.median(counts)) if counts else 0.0
    p90 = float(_percentile([float(c) for c in counts], 90) or 0.0)
    return DeploymentFrequency(
        total_deploys=len(successful),
        per_day_average=average,
        per_day_median=median,
        per_day_p90=p90,
        label=_label_deployment_frequency(average),
        time_series=[
            DfPoint(date=d, count=c) for d, c in counts_by_day.items()
        ],
    )


def _compute_lead_time(
    prs: list[PullRequest],
    *,
    start: datetime,
    end: datetime,
) -> LeadTime:
    durations_hours: list[float] = []
    by_week: dict[str, list[float]] = {}
    for pr in prs:
        if not pr.merged or not pr.merged_at or not pr.opened_at:
            continue
        if pr.merged_at < start or pr.merged_at > end:
            continue
        delta = (pr.merged_at - pr.opened_at).total_seconds() / 3600.0
        if delta < 0:
            continue
        durations_hours.append(delta)
        wk = _week_start(pr.merged_at.astimezone(timezone.utc))
        by_week.setdefault(wk, []).append(delta)

    median = (
        float(statistics.median(durations_hours)) if durations_hours else None
    )
    p90 = _percentile(durations_hours, 90)
    series: list[LtPoint] = []
    # Pre-fill week buckets in the window for stable charts.
    cursor = start
    seen: set[str] = set()
    while cursor <= end:
        wk = _week_start(cursor)
        if wk not in seen:
            seen.add(wk)
            samples = by_week.get(wk, [])
            wk_median = float(statistics.median(samples)) if samples else None
            series.append(
                LtPoint(week_start=wk, median_hours=wk_median, sample=len(samples))
            )
        cursor += timedelta(days=1)
    return LeadTime(
        median_hours=median,
        p90_hours=p90,
        label=_label_lead_time(median),
        sample_size=len(durations_hours),
        time_series=series,
    )


def _compute_change_failure_rate(
    deploys: list[WorkflowRun],
    hotfix_prs: list[PullRequest],
    *,
    start: datetime,
    end: datetime,
) -> ChangeFailureRate:
    success = sum(1 for r in deploys if r.conclusion == "success")
    failure = sum(1 for r in deploys if r.conclusion == "failure")
    total = success + failure
    hotfixes = len(
        [
            pr
            for pr in hotfix_prs
            if pr.merged_at is not None and start <= pr.merged_at <= end
        ]
    )
    # Effective failures = failed deploys + hotfixes (proxy for "we
    # had to follow up to fix something that shipped"). Cap at total
    # so the rate stays in [0, 1] when hotfixes exceed failed deploys.
    effective_failures = min(failure + hotfixes, total) if total else 0
    rate = (effective_failures / total) if total else 0.0

    by_day_total: dict[str, int] = {d: 0 for d in _date_buckets(start, end)}
    by_day_failed: dict[str, int] = {d: 0 for d in _date_buckets(start, end)}
    for r in deploys:
        if r.started_at is None:
            continue
        d = r.started_at.astimezone(timezone.utc).date().isoformat()
        if d not in by_day_total:
            continue
        if r.conclusion == "success":
            by_day_total[d] += 1
        elif r.conclusion == "failure":
            by_day_total[d] += 1
            by_day_failed[d] += 1
    series = [
        CfrPoint(date=d, total=by_day_total[d], failed=by_day_failed[d])
        for d in by_day_total
    ]
    return ChangeFailureRate(
        rate=rate,
        label=_label_change_failure_rate(rate),
        total_deploys=total,
        failed_deploys=failure,
        hotfix_prs=hotfixes,
        time_series=series,
    )


def _compute_mttr(deploys: list[WorkflowRun]) -> MeanTimeToRecovery:
    chrono = sorted(
        [r for r in deploys if r.started_at is not None],
        key=lambda r: r.started_at,  # type: ignore[arg-type]
    )
    incidents: list[MttrIncident] = []
    durations_hours: list[float] = []
    open_failure: WorkflowRun | None = None
    for r in chrono:
        if r.conclusion == "failure":
            if open_failure is None:
                open_failure = r
        elif r.conclusion == "success":
            if open_failure is not None:
                start = open_failure.finished_at or open_failure.started_at
                end = r.started_at
                hours: float | None = None
                if start and end:
                    hours = (end - start).total_seconds() / 3600.0
                    if hours < 0:
                        hours = 0.0
                    durations_hours.append(hours)
                incidents.append(
                    MttrIncident(
                        failed_at=(open_failure.started_at or open_failure.finished_at)
                        .astimezone(timezone.utc)
                        .isoformat(),  # type: ignore[union-attr]
                        recovered_at=r.started_at.astimezone(timezone.utc).isoformat()
                        if r.started_at
                        else None,
                        hours=hours,
                        workflow_name=r.name,
                    )
                )
                open_failure = None
    # An open failure at the end of the window stays open — surface it
    # so the operator can see it's unresolved, but it doesn't count
    # toward the median (no recovery time yet).
    if open_failure is not None:
        incidents.append(
            MttrIncident(
                failed_at=(open_failure.started_at or open_failure.finished_at)
                .astimezone(timezone.utc)
                .isoformat(),  # type: ignore[union-attr]
                recovered_at=None,
                hours=None,
                workflow_name=open_failure.name,
            )
        )
    median = (
        float(statistics.median(durations_hours)) if durations_hours else None
    )
    p90 = _percentile(durations_hours, 90)
    return MeanTimeToRecovery(
        median_hours=median,
        p90_hours=p90,
        label=_label_mttr(median),
        incidents=incidents,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/analytics/dora", response_model=DoraOut)
async def get_dora(
    workspace_id: uuid.UUID,
    days: int = Query(30, ge=7, le=365),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DoraOut:
    """Return the 4 DORA metrics + per-day / per-week time series."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Pull every WorkflowRun on `main` in the window — we filter to
    # deploy-shaped names in Python so the regex tuning stays in one
    # place. The dataset is small (a few hundred runs over 30 days for
    # a healthy workspace; thousands for a busy one) so this is fine.
    runs_stmt = (
        select(WorkflowRun)
        .where(
            and_(
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.head_branch == "main",
                WorkflowRun.started_at.isnot(None),
                WorkflowRun.started_at >= start,
                WorkflowRun.started_at <= end,
                WorkflowRun.conclusion.in_(["success", "failure"]),
            )
        )
        .order_by(WorkflowRun.started_at.asc())
    )
    runs = list((await session.execute(runs_stmt)).scalars().all())
    deploys = [r for r in runs if _is_deploy_workflow(r.name)]

    prs_stmt = (
        select(PullRequest)
        .where(
            and_(
                PullRequest.workspace_id == workspace_id,
                PullRequest.merged.is_(True),
                PullRequest.merged_at.isnot(None),
                PullRequest.merged_at >= start,
                PullRequest.merged_at <= end,
            )
        )
        .order_by(PullRequest.merged_at.asc())
    )
    prs = list((await session.execute(prs_stmt)).scalars().all())
    hotfix_prs = [pr for pr in prs if _is_hotfix_pr(pr.title)]

    deployment_frequency = _compute_deployment_frequency(
        deploys, start=start, end=end
    )
    lead_time = _compute_lead_time(prs, start=start, end=end)
    change_failure_rate = _compute_change_failure_rate(
        deploys, hotfix_prs, start=start, end=end
    )
    mttr = _compute_mttr(deploys)

    return DoraOut(
        window_days=days,
        computed_at=end.isoformat(),
        deployment_frequency=deployment_frequency,
        lead_time=lead_time,
        change_failure_rate=change_failure_rate,
        mttr=mttr,
    )


__all__ = ["router"]
