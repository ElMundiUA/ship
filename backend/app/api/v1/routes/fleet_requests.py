"""Workspace-level fan-out of agent requests (RFC-0008 §D).

The Console's ``/fleet/requests`` surface dispatches one catalog
pattern against many repos at once. This module owns:

- ``POST /v1/workspaces/{ws}/fleet/requests`` — validates the
  pattern + inputs once, then fans out one :class:`AgentRequest` per
  selected repo. Validation failures on individual repos are
  **best-effort** (RFC-0008 §D): the parent :class:`FleetRequest`
  lands with ``status=partial`` (or ``failed`` when nothing
  dispatched) and the response's ``rejections`` array tells the
  operator which repos to fix + retry.
- ``GET /v1/workspaces/{ws}/fleet/requests`` — list (newest first)
  for the Console's list page.
- ``GET /v1/workspaces/{ws}/fleet/requests/{id}`` — detail with the
  child pivot (repo × status × child ``AgentRequest`` id).
- ``POST /v1/workspaces/{ws}/fleet/requests/{id}/cancel`` — admin
  flips the parent into ``cancel_requested`` and marks every
  still-running child the same way. Actual GitHub Actions
  cancellation rides a follow-up PR — the flag is honoured
  optimistically so the Console can dim the affected runs
  immediately.

Pattern resolution + GitHub App lookup is delegated to the helpers
in :mod:`requests_api` so both surfaces share one validation path
and one set of error codes.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.requests_api import (
    _ADHOC_WORKFLOW_FILE,
    _ALLOWED_AGENT_SLUGS,
    AgentRequestIn,
    AgentRequestOut,
    _resolve_pattern_request,
    _serialize,
)
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import AgentRequest, FleetRequest
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["fleet-requests"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FleetRequestIn(AgentRequestIn):
    """Body for ``POST /fleet/requests``.

    Inherits the pattern-vs-ad-hoc dispatch shape from
    :class:`AgentRequestIn` so the exact same validator pipeline
    decides ``pattern_id`` + ``inputs`` once for the whole fan-out.
    Adds the per-fan-out extras:

    - ``repo_ids`` — at least one activated repo id. Each entry is
      validated against the workspace's activated-repos list; unknown
      ids land on ``rejections`` without blocking the rest.
    - ``title`` — optional display label for the Console list. When
      unset we fall back to the pattern's ``name`` (or the agent slug
      for ad-hoc dispatches).
    """

    repo_ids: list[uuid.UUID] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=256)


class FleetRequestRejection(BaseModel):
    """One repo that didn't make it into the fan-out.

    Emitted either because the repo id is unknown to the workspace
    (``repo_not_found``), the GitHub App is missing / suspended
    (``github_app_missing``), or GitHub rejected the
    ``workflow_dispatch`` call (``dispatch_failed``). For the first
    two, no child :class:`AgentRequest` row is created. For the
    third the child row is persisted with ``status=dispatch_failed``
    so retries / audit stay visible in the Console.
    """

    repo_id: uuid.UUID
    repo_full_name: str | None = None
    code: str
    message: str
    agent_request_id: uuid.UUID | None = None


class FleetRequestOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str | None
    pattern_id: str | None
    agent_slug: str | None
    inputs: dict[str, Any] = Field(default_factory=dict)
    context_ref: str | None
    status: str
    target_count: int
    dispatched_count: int
    rejected_count: int
    requested_by_email: str | None
    created_at: str
    updated_at: str


class FleetRequestCreateOut(BaseModel):
    fleet_request: FleetRequestOut
    children: list[AgentRequestOut]
    rejections: list[FleetRequestRejection]


class FleetRequestDetailOut(FleetRequestCreateOut):
    """Detail payload returned by ``GET /{id}``.

    Same shape as the create response so the Console can reuse the
    pivot component unchanged.
    """


class FleetRequestListOut(BaseModel):
    requests: list[FleetRequestOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _requester_email(
    session: AsyncSession, user_id: uuid.UUID | None
) -> str | None:
    if user_id is None:
        return None
    # Local import to avoid a circular dep during test collection when
    # ``tenancy`` is pulled in by the User model chain.
    from backend.app.db.models.tenancy import User

    user = await session.get(User, user_id)
    return user.email if user else None


def _serialize_fleet(
    row: FleetRequest,
    *,
    dispatched_count: int,
    rejected_count: int,
    requester_email: str | None,
) -> FleetRequestOut:
    return FleetRequestOut(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        pattern_id=row.pattern_id,
        agent_slug=row.agent_slug,
        inputs=dict(row.inputs or {}),
        context_ref=row.context_ref,
        status=row.status,
        target_count=row.target_count,
        dispatched_count=dispatched_count,
        rejected_count=rejected_count,
        requested_by_email=requester_email,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


# ---------------------------------------------------------------------------
# POST — fan-out dispatch
# ---------------------------------------------------------------------------


@router.post(
    "/fleet/requests",
    response_model=FleetRequestCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_fleet_request(
    workspace_id: uuid.UUID,
    payload: FleetRequestIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FleetRequestCreateOut:
    """Validate once, fan out to many repos (best-effort)."""
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        dispatch_workflow,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    if not payload.repo_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "missing_repo_ids",
                "message": "Pick at least one repo for the fan-out.",
            },
        )

    # Deduplicate while preserving order — operators can't hand-edit a
    # dispatch form into sending the same repo twice, but paste-heavy
    # UIs occasionally do.
    seen: set[uuid.UUID] = set()
    deduped_repo_ids: list[uuid.UUID] = []
    for rid in payload.repo_ids:
        if rid in seen:
            continue
        seen.add(rid)
        deduped_repo_ids.append(rid)

    # Pattern + input validation runs once. Failures here blow the
    # whole fan-out before anything lands — the operator needs to fix
    # the payload, not the repo selection.
    resolved = _resolve_pattern_request(payload)
    if resolved.agent_slug not in _ALLOWED_AGENT_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unknown_agent",
                "message": (
                    f"agent_slug must be one of {sorted(_ALLOWED_AGENT_SLUGS)}"
                ),
            },
        )

    # Pre-load every repo row in one query; the fan-out loop operates
    # on the in-memory list so we don't hammer the DB with
    # N round-trips.
    repo_rows = (
        (
            await session.execute(
                select(WorkspaceRepo).where(
                    WorkspaceRepo.workspace_id == workspace_id,
                    WorkspaceRepo.id.in_(deduped_repo_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    repo_by_id: dict[uuid.UUID, WorkspaceRepo] = {r.id: r for r in repo_rows}

    # Parent lands first so every child FK resolves. We stamp the
    # frozen payload on the parent so the pivot shows exactly what
    # dispatched even after the pattern evolves.
    fleet_row = FleetRequest(
        workspace_id=workspace_id,
        requested_by_user_id=auth.user.id,
        title=(
            payload.title
            or (resolved.pattern.name if resolved.pattern else None)
            or (resolved.pattern_id if resolved.pattern_id else resolved.agent_slug)
        ),
        pattern_id=resolved.pattern_id,
        agent_slug=resolved.agent_slug,
        inputs=dict(resolved.inputs),
        context_ref=resolved.context_ref,
        status="dispatching",
        target_count=len(deduped_repo_ids),
    )
    session.add(fleet_row)
    await session.flush()

    children: list[AgentRequestOut] = []
    rejections: list[FleetRequestRejection] = []

    for rid in deduped_repo_ids:
        repo_row = repo_by_id.get(rid)
        if repo_row is None:
            rejections.append(
                FleetRequestRejection(
                    repo_id=rid,
                    code="repo_not_found",
                    message="Repo not found in this workspace.",
                )
            )
            continue

        # Mirror the install-validation that ``requests_api._load_repo``
        # does, but don't raise on failure — collect the rejection and
        # keep going.
        if repo_row.installation_id is None:
            rejections.append(
                FleetRequestRejection(
                    repo_id=repo_row.id,
                    repo_full_name=repo_row.full_name,
                    code="github_app_missing",
                    message=(
                        "Ship's GitHub App isn't installed for this repo. "
                        "Reconnect it before dispatching requests."
                    ),
                )
            )
            continue
        install_row = await session.get(
            GitHubInstallation, repo_row.installation_id
        )
        if install_row is None or install_row.suspended_at is not None:
            rejections.append(
                FleetRequestRejection(
                    repo_id=repo_row.id,
                    repo_full_name=repo_row.full_name,
                    code="github_app_missing",
                    message=(
                        "Ship's GitHub App installation is missing or "
                        "suspended. Reinstall the Ship app."
                    ),
                )
            )
            continue

        child = AgentRequest(
            workspace_id=workspace_id,
            repo_id=repo_row.id,
            fleet_request_id=fleet_row.id,
            requested_by_user_id=auth.user.id,
            agent_slug=resolved.agent_slug,
            pattern_id=resolved.pattern_id,
            inputs=dict(resolved.inputs),
            context_ref=resolved.context_ref,
            prompt=resolved.prompt,
            status="dispatching",
        )
        session.add(child)
        await session.flush()

        workflow_inputs: dict[str, str] = {
            "agent": resolved.agent_slug,
            "prompt": resolved.prompt,
            "context_ref": resolved.context_ref or "",
            "ship_run_id": str(child.id),
            # Callback sink lands separately — see ``requests_api``.
            "ship_callback_url": "",
            "ship_run_token": "",
        }
        if resolved.pattern_id:
            workflow_inputs["pattern_id"] = resolved.pattern_id
            workflow_inputs["pattern_inputs_json"] = json.dumps(
                resolved.inputs, sort_keys=True
            )

        try:
            await dispatch_workflow(
                repo_row,
                install_row,
                _ADHOC_WORKFLOW_FILE,
                inputs=workflow_inputs,
                settings=settings,
            )
        except WorkflowDispatchError as exc:
            child.status = "dispatch_failed"
            child.summary = (
                exc.message or "GitHub rejected workflow_dispatch"
            )[:512]
            await session.flush()
            rejections.append(
                FleetRequestRejection(
                    repo_id=repo_row.id,
                    repo_full_name=repo_row.full_name,
                    code="dispatch_failed",
                    message=child.summary or "GitHub rejected the dispatch.",
                    agent_request_id=child.id,
                )
            )
            continue
        except httpx.HTTPStatusError as exc:
            child.status = "dispatch_failed"
            child.summary = (
                f"GitHub HTTP {exc.response.status_code} on "
                f"workflow_dispatch({_ADHOC_WORKFLOW_FILE})"
            )[:512]
            await session.flush()
            rejections.append(
                FleetRequestRejection(
                    repo_id=repo_row.id,
                    repo_full_name=repo_row.full_name,
                    code="dispatch_failed",
                    message=child.summary or "GitHub rejected the dispatch.",
                    agent_request_id=child.id,
                )
            )
            continue

        child.status = "dispatched"
        await session.flush()
        children.append(_serialize(child, repo_row.full_name, auth.user.email))

    # Roll up the parent. ``partial`` wins over ``dispatched`` whenever
    # any repo was rejected (either pre-flight or at dispatch time);
    # ``failed`` when nothing made it out.
    if len(children) == 0:
        fleet_row.status = "failed"
    elif rejections:
        fleet_row.status = "partial"
    else:
        fleet_row.status = "dispatched"
    # Persist pre-flight rejections so the detail view can rehydrate
    # them after a refresh. Dispatch-time failures already round-trip
    # via the child row's ``status=dispatch_failed``; skip those here
    # to avoid a double entry.
    fleet_row.rejections = [
        r.model_dump(mode="json")
        for r in rejections
        if r.code != "dispatch_failed"
    ]
    await session.flush()

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="fleet_request.dispatch",
            target_kind="fleet_request",
            target_id=str(fleet_row.id),
            payload={
                "pattern_id": resolved.pattern_id,
                "agent_slug": resolved.agent_slug,
                "target_count": len(deduped_repo_ids),
                "dispatched_count": len(children),
                "rejected_count": len(rejections),
                "repo_ids": [str(rid) for rid in deduped_repo_ids],
            },
        )
    )
    await session.flush()
    # Reload server-generated timestamps (``created_at`` + ``updated_at``)
    # explicitly — the JSONB assignment earlier triggers a fresh UPDATE,
    # which expires the columns so they'd otherwise lazy-load on
    # serialization (and lazy-load on an async session crosses the
    # greenlet boundary).
    await session.refresh(fleet_row, attribute_names=["created_at", "updated_at"])

    return FleetRequestCreateOut(
        fleet_request=_serialize_fleet(
            fleet_row,
            dispatched_count=len(children),
            rejected_count=len(rejections),
            requester_email=auth.user.email,
        ),
        children=children,
        rejections=rejections,
    )


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


@router.get("/fleet/requests", response_model=FleetRequestListOut)
async def list_fleet_requests(
    workspace_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetRequestListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    rows = (
        (
            await session.execute(
                select(FleetRequest)
                .where(FleetRequest.workspace_id == workspace_id)
                .order_by(desc(FleetRequest.created_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    # Compute dispatched/rejected counts per parent in one query.
    ids = [r.id for r in rows]
    counts_by_id: dict[uuid.UUID, tuple[int, int]] = {}
    if ids:
        from sqlalchemy import case, func

        stmt = (
            select(
                AgentRequest.fleet_request_id,
                func.count().label("total"),
                func.sum(
                    case(
                        (AgentRequest.status == "dispatch_failed", 1),
                        else_=0,
                    )
                ).label("failed"),
            )
            .where(AgentRequest.fleet_request_id.in_(ids))
            .group_by(AgentRequest.fleet_request_id)
        )
        for fleet_id, total, failed in (await session.execute(stmt)).all():
            total_i = int(total or 0)
            failed_i = int(failed or 0)
            counts_by_id[fleet_id] = (total_i - failed_i, failed_i)

    out: list[FleetRequestOut] = []
    for row in rows:
        dispatched, rejected = counts_by_id.get(row.id, (0, 0))
        # Include pre-flight rejections (target_count - total_children).
        total_children = dispatched + rejected
        preflight_rejected = max(0, row.target_count - total_children)
        email = await _requester_email(session, row.requested_by_user_id)
        out.append(
            _serialize_fleet(
                row,
                dispatched_count=dispatched,
                rejected_count=rejected + preflight_rejected,
                requester_email=email,
            )
        )
    return FleetRequestListOut(requests=out)


# ---------------------------------------------------------------------------
# GET detail
# ---------------------------------------------------------------------------


@router.get(
    "/fleet/requests/{fleet_request_id}",
    response_model=FleetRequestDetailOut,
)
async def get_fleet_request(
    workspace_id: uuid.UUID,
    fleet_request_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetRequestDetailOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    row = (
        await session.execute(
            select(FleetRequest).where(
                FleetRequest.id == fleet_request_id,
                FleetRequest.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fleet request not found.",
        )

    # Children with their repo full names for the pivot table.
    child_stmt = (
        select(AgentRequest, WorkspaceRepo.full_name)
        .join(WorkspaceRepo, AgentRequest.repo_id == WorkspaceRepo.id)
        .where(AgentRequest.fleet_request_id == row.id)
        .order_by(AgentRequest.created_at.asc())
    )
    child_rows = (await session.execute(child_stmt)).all()

    email = await _requester_email(session, row.requested_by_user_id)
    children = [
        _serialize(child, repo_full_name, email)
        for child, repo_full_name in child_rows
    ]
    dispatched = sum(
        1 for c in children if c.status not in {"dispatch_failed"}
    )
    rejected_children = len(children) - dispatched
    # Pre-flight rejections persisted on POST; dispatch-time failures
    # are rehydrated from the child rows so the pivot stays honest
    # even after the parent row has been serialized ages ago.
    rejections: list[FleetRequestRejection] = [
        FleetRequestRejection(**entry)
        for entry in (row.rejections or [])
    ]
    for child in children:
        if child.status == "dispatch_failed":
            rejections.append(
                FleetRequestRejection(
                    repo_id=child.repo_id,
                    repo_full_name=child.repo_full_name,
                    code="dispatch_failed",
                    message=child.summary or "GitHub rejected the dispatch.",
                    agent_request_id=child.id,
                )
            )
    preflight = len(row.rejections or [])

    return FleetRequestDetailOut(
        fleet_request=_serialize_fleet(
            row,
            dispatched_count=dispatched,
            rejected_count=rejected_children + preflight,
            requester_email=email,
        ),
        children=children,
        rejections=rejections,
    )


# ---------------------------------------------------------------------------
# POST cancel
# ---------------------------------------------------------------------------


@router.post(
    "/fleet/requests/{fleet_request_id}/cancel",
    response_model=FleetRequestDetailOut,
)
async def cancel_fleet_request(
    workspace_id: uuid.UUID,
    fleet_request_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FleetRequestDetailOut:
    """Flip the parent + every still-running child to ``cancel_requested``.

    The GitHub Actions cancel API isn't wired yet (tracked in a
    follow-up). For now we flip the DB state so the Console can dim
    the affected runs immediately; when the real canceler ships it
    picks these rows up and calls
    ``POST /repos/:owner/:repo/actions/runs/:run_id/cancel``.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    row = (
        await session.execute(
            select(FleetRequest).where(
                FleetRequest.id == fleet_request_id,
                FleetRequest.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fleet request not found.",
        )

    if row.status in {"cancel_requested", "cancelled"}:
        # Idempotent — return the current detail view.
        return await get_fleet_request(
            workspace_id=workspace_id,
            fleet_request_id=fleet_request_id,
            auth=auth,
            session=session,
        )

    row.status = "cancel_requested"
    # Mark live children so the pivot dims them immediately. We don't
    # touch terminal states (``dispatch_failed``) so the audit trail
    # keeps the original failure reason.
    children = (
        (
            await session.execute(
                select(AgentRequest).where(
                    AgentRequest.fleet_request_id == row.id,
                    AgentRequest.status.in_(["dispatching", "dispatched"]),
                )
            )
        )
        .scalars()
        .all()
    )
    for child in children:
        child.status = "cancel_requested"
    await session.flush()

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="fleet_request.cancel",
            target_kind="fleet_request",
            target_id=str(row.id),
            payload={
                "children_flipped": len(children),
            },
        )
    )
    await session.flush()

    return await get_fleet_request(
        workspace_id=workspace_id,
        fleet_request_id=fleet_request_id,
        auth=auth,
        session=session,
    )


__all__ = ["router"]
