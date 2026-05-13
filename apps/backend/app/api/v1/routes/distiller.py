"""Distiller v1 surface — read-only run history.

The write paths (``POST /buckets/{slug}/distill`` and
``POST /buckets/{slug}/upload``) are retired: workspace knowledge is
now produced solely through the harvester → router → synthesiser
pipeline, which calls ``run_distiller`` server-side without an HTTP
boundary. What remains here is the run-history reader so an operator
can audit what the synthesiser did against a bucket.

RBAC: ``ROLES_READ`` — history is workspace-wide info, no write
capability is exposed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.chat import _load_bucket
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import DistillerRun
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["distiller"],
)


class DistillerRunOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    bucket_id: uuid.UUID
    source_kind: str
    status: str
    decision: str | None
    input_ref: dict[str, Any]
    output_refs: dict[str, Any]
    error: str | None
    created_by_user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _run_to_out(row: DistillerRun) -> DistillerRunOut:
    return DistillerRunOut(
        id=row.id,
        workspace_id=row.workspace_id,
        bucket_id=row.bucket_id,
        source_kind=row.source_kind,
        status=row.status,
        decision=row.decision,
        input_ref=row.input_ref or {},
        output_refs=row.output_refs or {},
        error=row.error,
        created_by_user_id=row.created_by_user_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/buckets/{slug}/distill/runs",
    response_model=list[DistillerRunOut],
)
async def list_distiller_runs(
    workspace_id: uuid.UUID,
    slug: str,
    limit: int = 50,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[DistillerRunOut]:
    """Recent runs for the bucket, newest first.

    ``limit`` is clamped to [1, 200].
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    bucket = await _load_bucket(session, workspace_id, slug)
    safe_limit = max(1, min(int(limit), 200))
    rows = (
        await session.execute(
            select(DistillerRun)
            .where(DistillerRun.bucket_id == bucket.id)
            .order_by(desc(DistillerRun.created_at))
            .limit(safe_limit)
        )
    ).scalars().all()
    return [_run_to_out(row) for row in rows]
