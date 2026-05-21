"""Weekly file-overlap warning / honour metrics (ELS-156)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_READ, _require_membership
from backend.app.db.session import get_session
from backend.app.services.file_overlap_telemetry import weekly_file_overlap_metrics


router = APIRouter(
    prefix="/workspaces/{workspace_id}/metrics/file-overlap",
    tags=["metrics"],
)


class FileOverlapWeeklyOut(BaseModel):
    warnings_fired: int
    honoured: int
    ignored: int
    honour_rate: float | None
    window_days: int


@router.get("/weekly", response_model=FileOverlapWeeklyOut)
async def get_file_overlap_weekly(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> FileOverlapWeeklyOut:
    """Roll up overlap warnings and honour outcomes for the last 7 days."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    metrics = await weekly_file_overlap_metrics(
        session, workspace_id=workspace_id, days=7
    )
    return FileOverlapWeeklyOut(
        warnings_fired=metrics.warnings_fired,
        honoured=metrics.honoured,
        ignored=metrics.ignored,
        honour_rate=metrics.honour_rate,
        window_days=7,
    )
