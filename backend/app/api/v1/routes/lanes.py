"""Lanes API — list + sync customer-declared ``.ship/config.yml`` lanes (RFC-0007 Phase 7).

Three surfaces:

- ``GET /v1/workspaces/{ws}/lanes`` — workspace-wide list, optional
  ``?repo_id=`` filter. Powers the Console ``/lanes`` page.
- ``GET /v1/workspaces/{ws}/lanes/{lane_row_id}`` — detail including
  the 20 most recent :class:`PipelineRun` rows scoped to that lane
  (empty today; populated when "Trigger lane" lands).
- ``POST /v1/workspaces/{ws}/repos/{repo_id}/lanes/sync`` — admin
  trigger for a one-off re-pull of ``.ship/config.yml``. Idempotent.

Webhook-driven syncs (push to default branch touching
``.ship/config.yml``) are wired in :mod:`backend.app.api.v1.routes.github_app`;
this module is the human/Console-facing surface.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.fleet_lanes import FleetLane
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.lanes import Lane
from backend.app.db.models.pipelines import PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services.lanes_sync import (
    CONFIG_PATH,
    SyncReport,
    sync_lanes_for_repo,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["lanes"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


LaneScope = Literal["repo", "fleet"]


class LaneOut(BaseModel):
    """Unified row on the ``/lanes`` list — covers both repo-scoped and
    workspace fleet lanes (P1-09).

    The console reads one shape regardless of ``?scope=`` so list /
    detail components don't have to branch. Repo-only fields stay
    populated for ``scope='repo'`` rows; fleet-only fields populate
    for ``scope='fleet'`` rows. Common fields (``id``, ``workspace_id``,
    ``lane_id``, ``kind``, ``enabled``, timestamps) are always set.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    # Discriminator. ``repo`` rows mirror the legacy ``Lane`` shape;
    # ``fleet`` rows hydrate from :class:`FleetLane`.
    scope: LaneScope
    lane_id: str
    kind: str = Field(
        ...,
        description=(
            "``once`` | ``event`` | ``schedule`` for repo-scoped lanes; "
            "``mirror_lane`` (or future fleet kinds) for fleet lanes."
        ),
    )
    enabled: bool
    created_at: datetime
    updated_at: datetime

    # Repo-scoped fields — present iff ``scope == 'repo'``.
    repo_id: uuid.UUID | None = None
    repo_full_name: str | None = None
    pattern: str | None = None
    cron: str | None = None
    idempotency_key: str | None = None
    config: dict | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    synced_at: datetime | None = None
    sync_source: str | None = None

    # Fleet-scoped fields — present iff ``scope == 'fleet'``. Mirror
    # the columns on :class:`FleetLane` so the console never needs a
    # second round-trip to render either side.
    name: str | None = None
    pattern_id: str | None = None
    cadence: str | None = None
    agent_slug: str | None = None
    inputs: dict | None = None


class LaneListOut(BaseModel):
    """Envelope for ``GET /workspaces/{ws}/lanes``."""

    lanes: list[LaneOut]


class LaneRunOut(BaseModel):
    """Subset of :class:`PipelineRun` used on the lane detail view."""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    status: str
    trigger: str
    summary: str | None
    started_at: datetime | None
    finished_at: datetime | None


class LaneDetailOut(LaneOut):
    """Envelope for ``GET /workspaces/{ws}/lanes/{id}``."""

    recent_runs: list[LaneRunOut] = Field(default_factory=list)


class LaneSyncOut(BaseModel):
    """Envelope for ``POST /repos/{repo_id}/lanes/sync``."""

    repo_id: uuid.UUID
    added: int
    updated: int
    removed: int
    unchanged: int
    errors: list[str]
    sync_source: str | None


def _row_to_out(row: Lane, *, repo_full_name: str) -> LaneOut:
    return LaneOut(
        id=row.id,
        workspace_id=row.workspace_id,
        scope="repo",
        repo_id=row.repo_id,
        repo_full_name=repo_full_name,
        lane_id=row.lane_id,
        kind=row.kind,
        pattern=row.pattern,
        cron=row.cron,
        idempotency_key=row.idempotency_key,
        enabled=row.enabled,
        config=dict(row.config_blob or {}),
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        synced_at=row.synced_at,
        sync_source=row.sync_source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fleet_row_to_out(row: FleetLane) -> LaneOut:
    """Project a :class:`FleetLane` row onto the unified :class:`LaneOut`.

    Repo-scoped fields (``repo_id``, ``pattern``, ``cron``, ``config``,
    ``last_run_*``, ``sync_source``) stay ``None`` because fleet lanes
    aren't tied to a single repo or a synced ``.ship/config.yml`` file.
    """
    return LaneOut(
        id=row.id,
        workspace_id=row.workspace_id,
        scope="fleet",
        lane_id=row.lane_id,
        kind=row.kind,
        enabled=row.enabled,
        name=row.name,
        pattern_id=row.pattern_id,
        cadence=row.cadence,
        agent_slug=row.agent_slug,
        inputs=dict(row.inputs or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_scope_repo_xor(
    scope: str, repo_id: uuid.UUID | None
) -> None:
    """Enforce the ``scope`` ↔ ``repo_id`` invariant from P1-09.

    - ``scope='repo'`` **requires** ``repo_id`` (422 otherwise).
    - ``scope='fleet'`` **rejects** ``repo_id`` (422 — combination
      meaningless: fleet lanes are workspace-scoped).
    - ``scope='all'`` accepts ``repo_id`` as a *legacy filter*: when
      provided we narrow repo-scoped rows to that repo and still
      union with fleet lanes. This keeps every pre-P1-09 caller
      working unchanged (the original endpoint took bare
      ``?repo_id=``) while preserving the spec's strict combinations
      under explicit scopes.

    Raises ``HTTPException(422)`` with a human-readable detail string
    on violation. Centralised so lanes + pipelines share one error
    vocabulary for the same misuse.
    """
    if scope == "repo":
        if repo_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="repo_id is required when scope=repo",
            )
        return
    if scope == "fleet" and repo_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="repo_id is only valid when scope=repo",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/lanes", response_model=LaneListOut)
async def list_lanes_route(
    workspace_id: uuid.UUID,
    scope: Literal["all", "fleet", "repo"] = Query(
        default="all",
        description=(
            "``all`` (default) returns workspace + fleet lanes; ``fleet`` "
            "returns only :class:`FleetLane` rows projected onto the "
            "unified shape; ``repo`` returns only :class:`Lane` rows for "
            "the given ``repo_id`` (required when ``scope=repo``)."
        ),
    ),
    repo_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Workspace repo to narrow ``scope=repo`` results to. 422 when "
            "set together with ``scope=fleet`` / ``scope=all``."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> LaneListOut:
    """List lanes for the workspace under one of three scopes (P1-09).

    Default behaviour (``scope=all``, no ``repo_id``) is the union of
    repo-declared lanes and workspace fleet lanes — preserves the
    pre-P1-09 contract for callers that haven't moved off the
    legacy ``/lanes`` URL.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    _validate_scope_repo_xor(scope, repo_id)

    if scope == "repo":
        # ``repo_id`` is guaranteed not-None by the XOR validator. We
        # additionally pin it to the workspace so cross-tenant repo
        # ids return 422 rather than silently filtering to an empty
        # set (mirrors the spec's "must be a workspace_repo" rule).
        target_repo = (
            await session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == workspace_id,
                    WorkspaceRepo.id == repo_id,
                )
            )
        ).scalar_one_or_none()
        if target_repo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="repo_id must reference a repo in this workspace",
            )

    out: list[LaneOut] = []
    if scope in {"all", "repo"}:
        stmt = (
            select(Lane, WorkspaceRepo)
            .join(WorkspaceRepo, WorkspaceRepo.id == Lane.repo_id)
            .where(Lane.workspace_id == workspace_id)
            .order_by(WorkspaceRepo.full_name, Lane.lane_id)
        )
        if repo_id is not None:
            # Applies under scope=repo (validated above) AND under
            # scope=all when callers pass legacy ``?repo_id=`` to
            # narrow down without spelling out the new scope param.
            stmt = stmt.where(Lane.repo_id == repo_id)
        rows = (await session.execute(stmt)).all()
        out.extend(
            _row_to_out(lane, repo_full_name=repo.full_name)
            for (lane, repo) in rows
        )
    # Skip fleet lanes when the caller is filtering by a specific
    # repo: a repo filter implies "scope down to that repo's lanes",
    # and fleet lanes are workspace-wide, not repo-attached.
    if scope == "fleet" or (scope == "all" and repo_id is None):
        fleet_rows = (
            (
                await session.execute(
                    select(FleetLane)
                    .where(FleetLane.workspace_id == workspace_id)
                    .order_by(asc(FleetLane.created_at))
                )
            )
            .scalars()
            .all()
        )
        out.extend(_fleet_row_to_out(row) for row in fleet_rows)
    return LaneListOut(lanes=out)


@router.get("/lanes/{lane_row_id}", response_model=LaneDetailOut)
async def get_lane_route(
    workspace_id: uuid.UUID,
    lane_row_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> LaneDetailOut:
    """Single lane + recent runs."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = select(Lane, WorkspaceRepo).join(
        WorkspaceRepo, WorkspaceRepo.id == Lane.repo_id
    ).where(
        Lane.workspace_id == workspace_id,
        Lane.id == lane_row_id,
    )
    result = (await session.execute(stmt)).first()
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    lane_row, repo_row = result

    runs_stmt = (
        select(PipelineRun)
        .where(PipelineRun.lane_id == lane_row.id)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
        .limit(20)
    )
    runs = (await session.execute(runs_stmt)).scalars().all()

    base = _row_to_out(lane_row, repo_full_name=repo_row.full_name)
    return LaneDetailOut(
        **base.model_dump(),
        recent_runs=[
            LaneRunOut(
                id=r.id,
                pipeline_id=r.pipeline_id,
                status=r.status,
                trigger=r.trigger,
                summary=r.summary,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in runs
        ],
    )


@router.post(
    "/repos/{repo_id}/lanes/sync",
    response_model=LaneSyncOut,
)
async def sync_lanes_route(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LaneSyncOut:
    """Re-pull ``.ship/config.yml`` for one repo and upsert lanes.

    Admin-only — the sync itself is idempotent but we treat it as a
    write (it drops lanes that were removed from the YAML) to keep
    the RBAC surface honest.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Repo is not backed by a GitHub App installation; "
                "cannot pull .ship/config.yml."
            ),
        )

    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )

    try:
        report: SyncReport = await sync_lanes_for_repo(
            session=session,
            repo=repo,
            install=install,
            settings=settings,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{CONFIG_PATH} not found on {repo.full_name}@"
                f"{repo.default_branch}. Run `shipctl init` first."
            ),
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub Contents API rejected the request "
                f"(HTTP {exc.response.status_code})."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="lanes.sync",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={
                "added": report.added,
                "updated": report.updated,
                "removed": report.removed,
                "unchanged": report.unchanged,
                "errors": list(report.errors),
                "sync_source": report.sync_source,
            },
        )
    )
    await session.flush()

    return LaneSyncOut(
        repo_id=repo.id,
        added=report.added,
        updated=report.updated,
        removed=report.removed,
        unchanged=report.unchanged,
        errors=list(report.errors),
        sync_source=report.sync_source,
    )


__all__ = ["router"]
