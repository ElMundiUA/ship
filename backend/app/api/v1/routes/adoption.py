"""Workspace-level adoption funnel (RFC-0008 §E).

A read-only rollup the Console's ``/fleet/adoption`` page consumes
to answer "how far has Ship actually landed across these repos?"
The funnel is the workspace-unique primitive the user called out
when we separated Workspace Home from Repo Home: per-repo surfaces
can see *their* lanes and dispatches, but only the workspace view
can show "X of Y repos activated but never ran a lane".

Funnel stages (monotonic — a repo is always in the highest stage
it satisfies):

1. ``installed``  — ``WorkspaceRepo`` row exists (even if GitHub
   App is suspended; the intent was there).
2. ``activated``  — ``activated_at IS NOT NULL`` — the operator
   explicitly turned Ship on for this repo.
3. ``seeded``     — ``installed_bundle_version IS NOT NULL``, i.e.
   the wizard successfully committed the ``.ship/`` bundle.
4. ``first_run``  — at least one ``PipelineRun`` or
   ``WorkflowRun`` or ``AgentRequest`` row exists for the repo.
5. ``steady``     — at least one *successful* pipeline / workflow
   run inside ``window_days`` (default 14).

Flags (orthogonal to stage — a repo can be both ``steady`` and
``bundle_out_of_date``):

- ``install_missing``       — ``installation_id IS NULL`` or
  install row has ``suspended_at``. These repos will bounce every
  fleet dispatch pre-flight.
- ``bundle_out_of_date``    — seeded but ``installed_bundle_version``
  < current constant. Wizard rerun will fix.
- ``stuck``                 — activated more than ``window_days``
  days ago with zero runs *ever*. Signals an onboarding bounce.
- ``cold``                  — had runs historically but zero inside
  the window. Signals that a lane stopped firing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import (
    AgentRequest,
    PipelineRun,
    WorkflowRun,
)
from backend.app.db.session import get_session
from backend.app.services.seed_bundle import BUNDLE_VERSION


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["adoption"],
)


Stage = Literal[
    "installed",
    "activated",
    "seeded",
    "first_run",
    "steady",
]

_STAGE_ORDER: tuple[Stage, ...] = (
    "installed",
    "activated",
    "seeded",
    "first_run",
    "steady",
)


class AdoptionTotals(BaseModel):
    """Per-stage repo count. Counts are *cumulative* by design so
    the UI can draw a funnel — ``activated`` includes everything in
    ``seeded``/``first_run``/``steady`` and so on. ``stuck`` is a
    flag count, not a stage; kept here because the header reads
    more naturally alongside the funnel numbers."""

    installed: int
    activated: int
    seeded: int
    first_run: int
    steady: int
    stuck: int
    install_missing: int
    bundle_out_of_date: int
    cold: int


class AdoptionRepo(BaseModel):
    repo_id: uuid.UUID
    full_name: str
    preset: str | None
    installed_bundle_version: int | None
    current_bundle_version: int
    activated_at: str | None
    stage: Stage
    runs_in_window: int
    last_run_at: str | None
    successes_in_window: int
    success_rate_in_window: float | None
    flags: list[str] = Field(default_factory=list)


class AdoptionReport(BaseModel):
    workspace_id: uuid.UUID
    generated_at: str
    window_days: int
    current_bundle_version: int
    totals: AdoptionTotals
    repos: list[AdoptionRepo]


@router.get("/adoption", response_model=AdoptionReport)
async def get_adoption_report(
    workspace_id: uuid.UUID,
    window_days: int = Query(default=14, ge=1, le=180),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AdoptionReport:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    # Base repo list + install suspension status in one query.
    repo_rows = (
        (
            await session.execute(
                select(WorkspaceRepo, GitHubInstallation.suspended_at)
                .outerjoin(
                    GitHubInstallation,
                    GitHubInstallation.id == WorkspaceRepo.installation_id,
                )
                .where(WorkspaceRepo.workspace_id == workspace_id)
                .order_by(WorkspaceRepo.full_name.asc())
            )
        ).all()
    )

    # Per-repo run signals. Three source tables (PipelineRun hits
    # seed + lane executions, WorkflowRun hits every Actions run
    # including self-triggered ones, AgentRequest hits one-shot
    # dispatches) — the union gives us both "has run ever" +
    # "success in window".
    run_signals = await _collect_run_signals(
        session, workspace_id, window_start
    )

    repos_out: list[AdoptionRepo] = []
    counts: dict[str, int] = {s: 0 for s in _STAGE_ORDER}
    flag_counts: dict[str, int] = {
        "stuck": 0,
        "install_missing": 0,
        "bundle_out_of_date": 0,
        "cold": 0,
    }

    for repo, suspended_at in repo_rows:
        sig = run_signals.get(
            repo.id,
            _RepoSignal(
                total_runs=0,
                runs_in_window=0,
                successes_in_window=0,
                last_run_at=None,
            ),
        )

        install_missing = repo.installation_id is None or suspended_at is not None
        bundle_out_of_date = (
            repo.installed_bundle_version is not None
            and repo.installed_bundle_version < BUNDLE_VERSION
        )

        stage = _compute_stage(
            repo, has_any_run=sig.total_runs > 0, successes_in_window=sig.successes_in_window
        )

        # ``stuck`` applies only if activated long enough ago that a
        # healthy install should have produced a run by now. We
        # re-use ``window_days`` here so the threshold matches the
        # window the UI shows; tuning knobs later if needed.
        stuck = (
            stage == "activated"
            and repo.activated_at is not None
            and repo.activated_at < window_start
            and sig.total_runs == 0
        )
        # ``cold`` surfaces repos that *did* run at some point but
        # have gone silent inside the window. ``steady`` repos are
        # by definition not cold.
        cold = stage != "steady" and sig.total_runs > 0 and sig.runs_in_window == 0

        flags: list[str] = []
        if install_missing:
            flags.append("install_missing")
        if bundle_out_of_date:
            flags.append("bundle_out_of_date")
        if stuck:
            flags.append("stuck")
        if cold:
            flags.append("cold")

        # Cumulative funnel counts — bump every stage up to & including
        # the repo's current one.
        reached = _STAGE_ORDER.index(stage)
        for i in range(reached + 1):
            counts[_STAGE_ORDER[i]] += 1
        for f in flags:
            if f in flag_counts:
                flag_counts[f] += 1

        success_rate: float | None = None
        if sig.runs_in_window > 0:
            success_rate = round(
                sig.successes_in_window / sig.runs_in_window, 4
            )

        repos_out.append(
            AdoptionRepo(
                repo_id=repo.id,
                full_name=repo.full_name,
                preset=repo.preset,
                installed_bundle_version=repo.installed_bundle_version,
                current_bundle_version=BUNDLE_VERSION,
                activated_at=(
                    repo.activated_at.isoformat()
                    if repo.activated_at is not None
                    else None
                ),
                stage=stage,
                runs_in_window=sig.runs_in_window,
                last_run_at=(
                    sig.last_run_at.isoformat()
                    if sig.last_run_at is not None
                    else None
                ),
                successes_in_window=sig.successes_in_window,
                success_rate_in_window=success_rate,
                flags=flags,
            )
        )

    totals = AdoptionTotals(
        installed=counts["installed"],
        activated=counts["activated"],
        seeded=counts["seeded"],
        first_run=counts["first_run"],
        steady=counts["steady"],
        stuck=flag_counts["stuck"],
        install_missing=flag_counts["install_missing"],
        bundle_out_of_date=flag_counts["bundle_out_of_date"],
        cold=flag_counts["cold"],
    )

    return AdoptionReport(
        workspace_id=workspace_id,
        generated_at=now.isoformat(),
        window_days=window_days,
        current_bundle_version=BUNDLE_VERSION,
        totals=totals,
        repos=repos_out,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RepoSignal:
    """Aggregated activity for one repo: total-ever + in-window."""

    __slots__ = (
        "total_runs",
        "runs_in_window",
        "successes_in_window",
        "last_run_at",
    )

    def __init__(
        self,
        *,
        total_runs: int,
        runs_in_window: int,
        successes_in_window: int,
        last_run_at: datetime | None,
    ) -> None:
        self.total_runs = total_runs
        self.runs_in_window = runs_in_window
        self.successes_in_window = successes_in_window
        self.last_run_at = last_run_at


async def _collect_run_signals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    window_start: datetime,
) -> dict[uuid.UUID, _RepoSignal]:
    """Roll up per-repo activity across the three source tables.

    We don't UNION at the DB layer — three small grouped queries
    are cheaper than one large UNION with distinct timestamp
    columns, and keeping the aggregation in Python lets us tune
    stage heuristics without rewriting SQL.
    """
    signals: dict[uuid.UUID, _RepoSignal] = {}

    def _bump(
        repo_id: uuid.UUID,
        *,
        total_delta: int,
        window_delta: int,
        success_delta: int,
        last: datetime | None,
    ) -> None:
        sig = signals.get(repo_id)
        if sig is None:
            sig = _RepoSignal(
                total_runs=0,
                runs_in_window=0,
                successes_in_window=0,
                last_run_at=None,
            )
            signals[repo_id] = sig
        sig.total_runs += total_delta
        sig.runs_in_window += window_delta
        sig.successes_in_window += success_delta
        if last is not None and (
            sig.last_run_at is None or last > sig.last_run_at
        ):
            sig.last_run_at = last

    # --- PipelineRun: keyed off pipeline → workspace_id. Join to
    # the pipeline table would widen the query; instead we use the
    # denormalised ``workspace_id`` on the run itself and then join
    # the pipeline only for ``repo_id``.
    from backend.app.db.models.pipelines import Pipeline

    pr_stmt = (
        select(
            Pipeline.repo_id,
            func.count().label("total"),
            func.sum(
                case((PipelineRun.started_at >= window_start, 1), else_=0)
            ).label("in_window"),
            func.sum(
                case(
                    (
                        and_(
                            PipelineRun.started_at >= window_start,
                            PipelineRun.status == "succeeded",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("success"),
            func.max(PipelineRun.started_at).label("last_at"),
        )
        .join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
        .where(
            PipelineRun.workspace_id == workspace_id,
            Pipeline.repo_id.is_not(None),
        )
        .group_by(Pipeline.repo_id)
    )
    for repo_id, total, in_window, success, last_at in (
        await session.execute(pr_stmt)
    ).all():
        if repo_id is None:
            continue
        _bump(
            repo_id,
            total_delta=int(total or 0),
            window_delta=int(in_window or 0),
            success_delta=int(success or 0),
            last=last_at,
        )

    # --- WorkflowRun: direct repo_id FK. ``conclusion='success'``
    # is the success signal; ``created_at`` is close enough to a
    # "when did Ship see activity" timestamp for the funnel.
    wr_stmt = (
        select(
            WorkflowRun.repo_id,
            func.count().label("total"),
            func.sum(
                case((WorkflowRun.created_at >= window_start, 1), else_=0)
            ).label("in_window"),
            func.sum(
                case(
                    (
                        and_(
                            WorkflowRun.created_at >= window_start,
                            WorkflowRun.conclusion == "success",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("success"),
            func.max(WorkflowRun.created_at).label("last_at"),
        )
        .where(
            WorkflowRun.workspace_id == workspace_id,
            WorkflowRun.repo_id.is_not(None),
        )
        .group_by(WorkflowRun.repo_id)
    )
    for repo_id, total, in_window, success, last_at in (
        await session.execute(wr_stmt)
    ).all():
        if repo_id is None:
            continue
        _bump(
            repo_id,
            total_delta=int(total or 0),
            window_delta=int(in_window or 0),
            success_delta=int(success or 0),
            last=last_at,
        )

    # --- AgentRequest: one-shot dispatches. Counts as "activity"
    # but only ``status='succeeded'`` counts as a success.
    ar_stmt = (
        select(
            AgentRequest.repo_id,
            func.count().label("total"),
            func.sum(
                case((AgentRequest.created_at >= window_start, 1), else_=0)
            ).label("in_window"),
            func.sum(
                case(
                    (
                        and_(
                            AgentRequest.created_at >= window_start,
                            AgentRequest.status == "succeeded",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("success"),
            func.max(AgentRequest.created_at).label("last_at"),
        )
        .where(AgentRequest.workspace_id == workspace_id)
        .group_by(AgentRequest.repo_id)
    )
    for repo_id, total, in_window, success, last_at in (
        await session.execute(ar_stmt)
    ).all():
        if repo_id is None:
            continue
        _bump(
            repo_id,
            total_delta=int(total or 0),
            window_delta=int(in_window or 0),
            success_delta=int(success or 0),
            last=last_at,
        )

    return signals


def _compute_stage(
    repo: WorkspaceRepo,
    *,
    has_any_run: bool,
    successes_in_window: int,
) -> Stage:
    if successes_in_window > 0:
        return "steady"
    if has_any_run:
        return "first_run"
    if repo.installed_bundle_version is not None:
        return "seeded"
    if repo.activated_at is not None:
        return "activated"
    return "installed"


__all__ = ["router"]
