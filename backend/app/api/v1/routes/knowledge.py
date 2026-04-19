"""Workspace-scoped knowledge buckets.

Reads from :mod:`backend.app.services.knowledge_lister` which scans every
registered :class:`backend.app.db.models.tenancy.ArtifactRepo` for
``.ship/knowledge/*.md`` files. Today these are the documents emitted by
:mod:`backend.app.services.knowledge_seeder` during onboarding.

The endpoint shape is intentionally close to ``workspace_artifacts``:
``GET /v1/workspaces/{ws_id}/knowledge`` returns ``{buckets: [...]}`` and
``GET /v1/workspaces/{ws_id}/knowledge/{slug}`` returns the same entry plus
``body`` (the full markdown). This lets the operator console show a list
view + a detail view without inventing any new wire conventions.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.knowledge_lister import (
    bucket_to_dict,
    get_bucket,
    list_buckets,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge",
    tags=["knowledge"],
)


async def _load_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> Workspace:
    await _require_membership(session, workspace_id, user_id, ROLES_READ)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return workspace


@router.get("")
async def list_workspace_knowledge(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)
    buckets = await list_buckets(session, workspace)
    return {
        "version": 1,
        "workspace_id": str(workspace.id),
        "buckets": [bucket_to_dict(b) for b in buckets],
    }


@router.get("/{slug}")
async def get_workspace_knowledge(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)
    bucket = await get_bucket(session, workspace, slug)
    if bucket is None:
        raise HTTPException(
            status_code=404,
            detail=f"knowledge bucket '{slug}' not found in any enabled repo",
        )
    return bucket_to_dict(bucket)
