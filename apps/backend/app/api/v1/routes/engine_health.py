"""GET /v1/workspaces/{ws}/engine-health — the thesis-2 residue surface.

Read-only: derives liveness + stalls from agent_dispatch_locks +
audit_log on request (see services/engine_health.py). Never dispatches,
never mutates a lock. Authorized per-workspace like every other
/v1/workspaces route.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_READ, _require_membership
from backend.app.db.session import get_session
from backend.app.services.engine_health import (
    DEFAULT_DISPATCH_AUDIT_WINDOW_MINUTES,
    DEFAULT_LOCK_STALL_MINUTES,
    assess_engine_health,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["engine-health"])


class StalledTicketOut(BaseModel):
    lock_key: str
    claimed_at: datetime
    expires_at: datetime
    age_minutes: float
    reason: str
    run_id: int | None


class EngineHealthOut(BaseModel):
    healthy: bool
    last_dispatch_at: datetime | None
    last_finish_at: datetime | None
    active_locks: int
    expired_unswept_locks: int
    stalled: list[StalledTicketOut]


@router.get("/engine-health", response_model=EngineHealthOut)
async def get_engine_health(
    workspace_id: uuid.UUID,
    lock_stall_minutes: int = Query(
        default=DEFAULT_LOCK_STALL_MINUTES, ge=5, le=24 * 60
    ),
    dispatch_window_minutes: int = Query(
        default=DEFAULT_DISPATCH_AUDIT_WINDOW_MINUTES, ge=5, le=24 * 60
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> EngineHealthOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    liveness = await assess_engine_health(
        session,
        workspace_id=workspace_id,
        lock_stall_minutes=lock_stall_minutes,
        dispatch_window_minutes=dispatch_window_minutes,
    )
    return EngineHealthOut(
        healthy=liveness.healthy,
        last_dispatch_at=liveness.last_dispatch_at,
        last_finish_at=liveness.last_finish_at,
        active_locks=liveness.active_locks,
        expired_unswept_locks=liveness.expired_unswept_locks,
        stalled=[StalledTicketOut(**vars(s)) for s in liveness.stalled],
    )
