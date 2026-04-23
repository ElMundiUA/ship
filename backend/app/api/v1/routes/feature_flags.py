"""Workspace-level feature flag toggle API (P2-19).

Two endpoints, both under ``/v1/workspaces/{workspace_id}/feature-flags``:

- ``GET`` — any workspace member can read the full flag map
  (defaults included). Powers the admin UI's flag overview without
  forcing the caller to know which flags exist.
- ``PUT /{flag_name}`` — workspace **owner** only (not admin — this
  is a rollout-safety lever and we don't want a misclick from a
  newly-promoted admin to flip the new inbox off mid-incident).
  Validates the flag against
  :data:`backend.app.services.feature_flags.FEATURE_FLAG_DEFAULTS`
  (422 on unknown), persists, and writes a single
  ``feature_flag.set`` audit row.

Wiring: adding the routes ships the *mechanism*. Per the P2-19 spec
we explicitly do **not** gate any inbox endpoints on the helper
yet — flipping the gate is a follow-up ticket.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.session import get_session
from backend.app.services import feature_flags as ff_service


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["feature-flags"],
)


# Owner-only — even ``admin`` is rejected. Centralised so the spec
# guarantee ("write access = workspace owner only") is enforced from
# one place.
_ROLES_OWNER: tuple[str, ...] = ("owner",)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FeatureFlagsOut(BaseModel):
    """``GET`` response: flag → bool dict including defaults."""

    flags: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "All recognised feature flags resolved to their effective "
            "value (persisted override or registry default). New "
            "workspaces with nothing persisted still see the full set."
        ),
    )


class FeatureFlagSetIn(BaseModel):
    enabled: bool = Field(
        ...,
        description=(
            "Target value. ``True`` enables the flag; ``False`` "
            "explicitly disables it (overrides the registry default)."
        ),
    )


class FeatureFlagSetOut(BaseModel):
    """``PUT`` response: the flag's new effective value + previous one.

    ``previous`` is included so the console can show "you turned this
    OFF" without a follow-up GET. Matches the value :func:`set_flag`
    returns from the service layer.
    """

    flag: str
    enabled: bool
    previous: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/feature-flags", response_model=FeatureFlagsOut)
async def get_workspace_feature_flags(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FeatureFlagsOut:
    """Return every recognised flag's current value for the workspace.

    Read access = workspace member. Unset flags read as their
    registry default (e.g. ``inbox_v1_enabled`` defaults to True).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    flags = await ff_service.get_flags(session, workspace_id)
    return FeatureFlagsOut(flags=flags)


@router.put(
    "/feature-flags/{flag_name}",
    response_model=FeatureFlagSetOut,
    status_code=status.HTTP_200_OK,
)
async def set_workspace_feature_flag(
    workspace_id: uuid.UUID,
    flag_name: str,
    payload: FeatureFlagSetIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FeatureFlagSetOut:
    """Flip a workspace-level feature flag.

    Owner-only. Returns the new effective value alongside the
    previous one (same data the service layer audited). Unknown flag
    names land on a 422 from
    :func:`backend.app.services.feature_flags.set_flag` so the
    console can render an inline form error.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, _ROLES_OWNER
    )
    previous = await ff_service.set_flag(
        session,
        workspace_id,
        flag_name,
        payload.enabled,
        actor_user_id=auth.user.id,
        actor_token_id=auth.token.id if auth.token else None,
    )
    return FeatureFlagSetOut(
        flag=flag_name,
        enabled=payload.enabled,
        previous=previous,
    )


__all__ = ["router"]
