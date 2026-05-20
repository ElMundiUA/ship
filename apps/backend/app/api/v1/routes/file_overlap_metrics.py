"""Weekly file-overlap telemetry rollup (ELS-156 / A5.3)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_READ, _require_membership
from backend.app.db.session import get_session
from backend.app.services.file_overlap_telemetry import weekly_file_overlap_metrics

router = APIRouter(
    prefix="/workspaces/{workspace_id}/metrics",
    tags=["metrics"],
)


class FileOverlapMetricsOut(BaseModel):
    window_days: int
    warnings_fired: int
    honoured: int
    ignored: int
    honour_rate: float | None


@router.get("/file-overlap", response_model=FileOverlapMetricsOut)
async def get_file_overlap_metrics(
    workspace_id: uuid.UUID,
    days: int = Query(default=7, ge=1, le=90),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FileOverlapMetricsOut:
    """Last-N-day overlap warning counts and honour rate."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    raw = await weekly_file_overlap_metrics(
        session, workspace_id=workspace_id, days=days
    )
    return FileOverlapMetricsOut(**raw)
