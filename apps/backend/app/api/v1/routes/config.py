"""HTTP surface for the per-workspace config registry.

Mirror of the agent ``config_help`` / ``config_put`` tools so the
console UI doesn't need to drive scope CRUD through chat. Same
backing module (``backend.app.services.config_registry``) — adding
a new scope there picks it up in both surfaces simultaneously.

Three endpoints, all workspace-scoped + auth-gated:

* ``GET  /v1/workspaces/{ws}/config``             → list every registered scope
* ``GET  /v1/workspaces/{ws}/config/{scope}``     → JSONSchema + current value
* ``PUT  /v1/workspaces/{ws}/config/{scope}``     → validate + apply + audit

The PUT path requires admin role (mutating). GETs require any
workspace member — settings UI needs to read defaults for the
operator before they decide whether to touch anything.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.config_registry import (
    SCOPES,
    help_scope,
    list_scopes,
    put_scope,
)


# NOTE: no ``/v1`` here — ``api_router`` already mounts every child under
# ``/v1``. A self-prefix would double it (``/v1/v1/…``) and strand the
# routes where no client looks (the pre-ELS-236 state of this file).
router = APIRouter(prefix="/workspaces/{workspace_id}/config", tags=["config"])


# ---------------------------------------------------------------------------
# Pydantic shapes
# ---------------------------------------------------------------------------


class ConfigScopeListOut(BaseModel):
    """Lightweight per-scope row for the index endpoint."""

    slug: str
    description: str


class ConfigScopeDetailOut(BaseModel):
    """Full scope detail used by the form renderer."""

    slug: str
    description: str
    # Pydantic's BaseModel reserves ``schema`` as a classmethod, so
    # rename to ``json_schema`` on the wire. The console form-renderer
    # uses this name verbatim.
    json_schema: dict[str, Any] = Field(alias="json_schema")
    current_value: Any


class ConfigPutIn(BaseModel):
    """PUT body — value shape is scope-specific."""

    value: Any = Field(default=None)


class ConfigPutOut(BaseModel):
    slug: str
    value: Any
    audit_event: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ConfigScopeListOut])
async def list_workspace_config_scopes(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ConfigScopeListOut]:
    """Index of every registered scope. Used by the settings landing
    page to render one card per scope without hard-coding the list."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return [
        ConfigScopeListOut(slug=row["slug"], description=row["description"])
        for row in list_scopes()
    ]


@router.get("/{scope}", response_model=ConfigScopeDetailOut)
async def get_workspace_config_scope(
    workspace_id: uuid.UUID,
    scope: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ConfigScopeDetailOut:
    """Return one scope's JSONSchema + current value. The console
    form-renderer drives the FE off this exact shape, so adding a
    new scope server-side (one ``ConfigScope`` row) automatically
    shows up in the UI without an FE change.

    404 when the scope isn't registered — we deliberately surface
    the unknown name rather than 422 because the FE is more likely
    to be out of sync with a freshly-deployed backend than to send a
    typo.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    if scope not in SCOPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_scope",
                "message": (
                    f"scope {scope!r} is not registered. GET /config "
                    "to list available scopes."
                ),
            },
        )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:  # pragma: no cover — membership check would 404 first
        raise HTTPException(status_code=404, detail="workspace not found")
    payload = await help_scope(session, workspace, scope)
    return ConfigScopeDetailOut(
        slug=payload["slug"],
        description=payload["description"],
        json_schema=payload["schema"],
        current_value=payload["current_value"],
    )


@router.put("/{scope}", response_model=ConfigPutOut)
async def put_workspace_config_scope(
    workspace_id: uuid.UUID,
    scope: str,
    body: ConfigPutIn = Body(...),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ConfigPutOut:
    """Validate + apply a new value. Admin-gated and audited under
    the scope's canonical action name (e.g.
    ``workspace.agent_provider.set``) so historical oncall queries
    keep matching."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if scope not in SCOPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "unknown_scope",
                "message": f"scope {scope!r} is not registered.",
            },
        )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    try:
        result = await put_scope(
            session,
            workspace,
            scope,
            body.value,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
        )
    except ValueError as exc:
        # Validation rejections from the scope writer — surface the
        # message so the console can show "agent_provider must be
        # one of …" verbatim rather than a generic "422".
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_value", "message": str(exc)},
        ) from exc
    return ConfigPutOut(**result)
