"""Workspace prose-rule policies API (Workspace policy injection).

CRUD over :class:`WorkspacePolicy` rows that get rendered into the
agent's system prompt at runtime (Navigator chat) and into the
``shipctl run`` stdout (GitHub-Actions agent step). Endpoints are
plain ``/workspaces/{ws}/policies``; admins author + toggle, all
members can read.

Naming history: the path was previously the home of mirror-lane
rules; that primitive moved to ``/workspaces/{ws}/fleet-lanes`` in
the Phase-1 rename. Net result is two distinct, well-named
surfaces — Fleet lanes for "wire pattern X as scheduled lane Y on
every repo" and Policies for "prose rule injected into every agent
turn".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.policies import WorkspacePolicy
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["policies"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_ROLE_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class PolicyOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    body: str
    enabled: bool
    sort_order: int
    # ``None`` means global (renders for every role + Navigator chat).
    # A non-empty list scopes the rule. The renderer also treats an
    # empty list as global (admin edits the multi-select down to zero
    # mustn't accidentally silence a rule), but we serialise ``[]`` as
    # ``None`` so the Console knows to clear the chip.
    applies_to_roles: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class PolicyListOut(BaseModel):
    policies: list[PolicyOut]


class PolicyCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    body: str = Field(..., min_length=1)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)
    applies_to_roles: list[str] | None = Field(
        default=None,
        description=(
            "Role slugs the policy targets. ``null`` (or omitted) "
            "means global. A non-empty list scopes the rule. Each "
            "slug must match ``^[a-z0-9][a-z0-9_-]*$``."
        ),
    )


class PolicyUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=-10_000, le=10_000)
    # PATCH semantics: omit the key to leave the scope unchanged;
    # send ``null`` to clear the scope (back to global). The
    # ``_PATCH_SENTINEL`` machinery below distinguishes "field
    # missing" from "field is null".
    applies_to_roles: list[str] | None = Field(default=None)


def _validate_role_slugs(slugs: list[str] | None) -> list[str] | None:
    """Normalise + validate a role-slug list. Returns ``None`` for the
    empty / null forms so the column matches the renderer's "global"
    semantic instead of round-tripping a stray empty array."""
    if slugs is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    import re

    for raw in slugs:
        if not isinstance(raw, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_role_slug",
                    "message": "applies_to_roles must contain strings only.",
                },
            )
        slug = raw.strip()
        if not re.match(_ROLE_SLUG_PATTERN, slug):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_role_slug",
                    "message": (
                        f"Role slug {slug!r} must match "
                        f"{_ROLE_SLUG_PATTERN}."
                    ),
                },
            )
        if slug in seen:
            continue
        seen.add(slug)
        cleaned.append(slug)
    return cleaned or None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/policies", response_model=PolicyListOut)
async def list_policies(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        (
            await session.execute(
                select(WorkspacePolicy)
                .where(WorkspacePolicy.workspace_id == workspace_id)
                .order_by(
                    asc(WorkspacePolicy.sort_order),
                    asc(WorkspacePolicy.created_at),
                )
            )
        )
        .scalars()
        .all()
    )
    return PolicyListOut(policies=[_serialise(r) for r in rows])


class PoliciesPreambleOut(BaseModel):
    """Workspace policies rendered as a markdown preamble.

    ``preamble`` is ``None`` when the workspace has no enabled
    policies that match the requested scope — the CLI uses that as
    the signal to skip prepending anything to the agent prompt
    (avoiding a stray separator).
    """

    preamble: str | None = Field(default=None)


@router.get(
    "/policies/preamble",
    response_model=PoliciesPreambleOut,
)
async def get_workspace_policies_preamble(
    workspace_id: uuid.UUID,
    role: str | None = Query(
        default=None,
        max_length=64,
        pattern=_ROLE_SLUG_PATTERN,
        description=(
            "Specialist role slug the agent run is acting as (e.g. "
            "``developer``, ``ba``, ``intake``). When provided, the "
            "preamble includes role-scoped policies in addition to "
            "globals; when omitted, only globals render."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PoliciesPreambleOut:
    """Return the workspace's policies as a ready-to-prepend preamble.

    Workspace-token auth — workspace members only. Cloud-agent runs
    via ``shipctl run`` call this with their role slug so the
    rendered prompt carries the same policy block as the Navigator
    chat. Auth boundary is membership, not the per-run JWT, because
    the CLI flow mints its ``run_id`` locally and has no JWT to
    present.
    """

    from backend.app.services.policies import render_policies_preamble

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    preamble = await render_policies_preamble(
        session, workspace_id, role_slug=role
    )
    return PoliciesPreambleOut(preamble=preamble)


@router.post(
    "/policies",
    response_model=PolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy(
    workspace_id: uuid.UUID,
    payload: PolicyCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = WorkspacePolicy(
        workspace_id=workspace_id,
        title=payload.title.strip(),
        body=payload.body,
        enabled=payload.enabled,
        sort_order=payload.sort_order,
        applies_to_roles=_validate_role_slugs(payload.applies_to_roles),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, attribute_names=["created_at", "updated_at"])
    return _serialise(row)


@router.patch("/policies/{policy_id}", response_model=PolicyOut)
async def update_policy(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    payload: PolicyUpdateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _require_policy(session, workspace_id, policy_id)
    if payload.title is not None:
        row.title = payload.title.strip()
    if payload.body is not None:
        row.body = payload.body
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    # PATCH semantic for ``applies_to_roles``: presence in
    # ``model_fields_set`` means the client explicitly sent the key
    # (either with a list to scope, or with ``null`` to clear back to
    # global). Absence means leave it alone.
    if "applies_to_roles" in payload.model_fields_set:
        row.applies_to_roles = _validate_role_slugs(payload.applies_to_roles)
    await session.flush()
    await session.refresh(row, attribute_names=["updated_at"])
    return _serialise(row)


@router.delete(
    "/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_policy(
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _require_policy(session, workspace_id, policy_id)
    await session.delete(row)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_policy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    policy_id: uuid.UUID,
) -> WorkspacePolicy:
    row = (
        await session.execute(
            select(WorkspacePolicy).where(
                WorkspacePolicy.id == policy_id,
                WorkspacePolicy.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "policy_not_found", "message": "Unknown policy."},
        )
    return row


def _serialise(row: WorkspacePolicy) -> PolicyOut:
    # Empty array → ``None`` so the Console treats both forms as
    # "global" without an extra branch on the client side.
    scope = row.applies_to_roles if row.applies_to_roles else None
    return PolicyOut(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        body=row.body,
        enabled=row.enabled,
        sort_order=row.sort_order,
        applies_to_roles=scope,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


__all__ = ["router"]
