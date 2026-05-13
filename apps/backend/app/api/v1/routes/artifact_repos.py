"""Artifact source repos for a workspace (RFC-0006).

A workspace can register one or more :class:`ArtifactRepo` rows. Each entry
points at a place artifacts live — currently a local filesystem path
(``file:///…``, read inline by the resolver). The legacy git-sync worker
that cloned remote URLs is gone; remote URLs are accepted on the schema
level (so existing rows don't break) but are invisible to the resolver
until the upcoming GitHub App integration replaces them with installation
IDs and Trees-API reads.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.schemas import ArtifactRepoCreate, ArtifactRepoOut
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.tenancy import ArtifactRepo, AuditLog
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}/artifact-repos",
    tags=["artifact-repos"],
)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    # Accept file://, https://, http://, ssh://, git+…://, or scp-style git@host:path.
    if parsed.scheme in {"file", "http", "https", "ssh", "git", "git+ssh", "git+https"}:
        return
    # scp-style: "git@github.com:owner/repo.git"
    if "@" in url and ":" in url and url.split(":", 1)[1] != "":
        return
    raise HTTPException(
        status_code=422,
        detail="url must be file://, https://, http://, ssh://, git@host:path, …",
    )


@router.get("", response_model=list[ArtifactRepoOut])
async def list_artifact_repos(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ArtifactRepoOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(ArtifactRepo)
        .where(ArtifactRepo.workspace_id == workspace_id)
        .order_by(ArtifactRepo.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [ArtifactRepoOut.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=ArtifactRepoOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_artifact_repo(
    workspace_id: uuid.UUID,
    payload: ArtifactRepoCreate,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ArtifactRepoOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    _validate_url(payload.url)

    repo = ArtifactRepo(
        workspace_id=workspace_id,
        kind=payload.kind,
        url=payload.url,
        default_branch=payload.default_branch,
    )
    session.add(repo)
    await session.flush()

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="artifact_repo.create",
            target_kind="artifact_repo",
            target_id=str(repo.id),
            payload={"kind": repo.kind, "url": repo.url},
        )
    )
    await session.flush()
    return ArtifactRepoOut.model_validate(repo)


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact_repo(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo = await session.get(ArtifactRepo, repo_id)
    if repo is None or repo.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="artifact repo not found")
    await session.delete(repo)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="artifact_repo.delete",
            target_kind="artifact_repo",
            target_id=str(repo_id),
            payload={"kind": repo.kind, "url": repo.url},
        )
    )
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
