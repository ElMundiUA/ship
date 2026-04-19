"""Artifact source repos for a workspace (RFC-0006).

A workspace can register one or more :class:`ArtifactRepo` rows. Each entry
points at a place artifacts live — either a local filesystem path
(``file:///…``, read inline by the resolver) or a git URL that the sync
worker (:mod:`backend.app.workers.git_sync`) clones into a local cache.
The ``POST /sync`` endpoint forces an immediate sync for a single repo so
the operator gets feedback right after registering one.
"""

from __future__ import annotations

import asyncio
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
from backend.app.services.git_sync import (
    apply_outcome,
    is_remote_url,
    sync_repo,
)


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
    if is_remote_url(payload.url):
        # Mark as not-yet-synced so the UI can show "Pending sync" while the
        # cron worker (or a manual /sync POST) catches up. The worker will
        # overwrite ``last_sync_at`` on the next tick.
        repo.last_sync_error = "pending first sync"
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


@router.post("/{repo_id}/sync", response_model=ArtifactRepoOut)
async def sync_artifact_repo(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ArtifactRepoOut:
    """Sync this repo right now and return the updated row.

    Convenience wrapper around the cron worker so an operator who just
    registered a repo doesn't have to wait for the next tick. ``file://``
    repos respond immediately with no-op since they're read inline.

    Permission: same as the rest of artifact-repo writes (admin+).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo = await session.get(ArtifactRepo, repo_id)
    if repo is None or repo.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="artifact repo not found")

    # ``sync_repo`` is a blocking subprocess (git clone/fetch); offload so we
    # don't pin the event loop for ~seconds at a time.
    outcome = await asyncio.to_thread(sync_repo, repo)
    apply_outcome(repo, outcome)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="artifact_repo.sync",
            target_kind="artifact_repo",
            target_id=str(repo_id),
            payload={
                "ok": outcome.error is None,
                "head_sha": outcome.head_sha,
                "error": outcome.error,
            },
        )
    )
    await session.flush()
    if outcome.error and not outcome.head_sha:
        # Surface failure with a 502: the row is still updated (so the
        # operator can see the error in the UI) but the call clearly didn't
        # bring a working clone online.
        raise HTTPException(status_code=502, detail=outcome.error)
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
