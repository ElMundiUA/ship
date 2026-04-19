"""Workspace-scoped artifact catalog (RFC-0006).

Reads through :mod:`backend.app.services.artifact_resolver`, which honours
the workspace's ``catalog_sources`` toggles and merges
project → workspace → global with the higher-priority source winning when
the same ``(kind, id)`` exists in multiple layers.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.artifact_loader import KIND_PLURALS
from backend.app.services.artifact_resolver import get_with_layers, list_kind


router = APIRouter(
    prefix="/workspaces/{workspace_id}/artifacts",
    tags=["workspace-artifacts"],
)


def _kind_or_400(kind: str) -> str:
    # Accept either singular ("pattern") or plural ("patterns") in the path so
    # the URL feels natural either way.
    if kind in KIND_PLURALS:
        return kind
    inverse = {v: k for k, v in KIND_PLURALS.items()}
    if kind in inverse:
        return inverse[kind]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"unknown artifact kind: {kind}",
    )


async def _load_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> Workspace:
    await _require_membership(session, workspace_id, user_id, ROLES_READ)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return workspace


@router.get("/{kind}")
async def list_workspace_artifacts(
    workspace_id: uuid.UUID,
    kind: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)
    canonical = _kind_or_400(kind)
    entries = await list_kind(session, workspace, canonical)
    plural = KIND_PLURALS[canonical]
    return {
        "version": 2,
        "kind": canonical,
        "workspace_id": str(workspace.id),
        "catalog_sources": workspace.catalog_sources,
        plural: [_public(e) for e in entries],
    }


_INTERNAL_FIELDS = ("_body", "_full")


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip resolver-internal keys before serialising to the wire."""
    return {k: v for k, v in entry.items() if k not in _INTERNAL_FIELDS}


@router.get("/{kind}/{artifact_id}")
async def get_workspace_artifact(
    workspace_id: uuid.UUID,
    kind: str,
    artifact_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)
    canonical = _kind_or_400(kind)
    bundle = await get_with_layers(session, workspace, canonical, artifact_id)
    if bundle is None:
        raise HTTPException(
            status_code=404,
            detail=f"{canonical} '{artifact_id}' not found in any enabled catalog source",
        )
    winner = bundle["winner"]
    layers = bundle["layers"]
    return {
        **_public(winner),
        # README rendered straight from artifacts/<plural>/<id>/ARTIFACT.md so
        # the UI doesn't need a second round-trip to the source repo.
        "readme": winner.get("_body") or "",
        "layers": [_public(layer) for layer in layers],
    }
