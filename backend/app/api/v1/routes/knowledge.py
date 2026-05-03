"""Workspace-scoped DB knowledge buckets."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import get_settings
from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import Workspace
from backend.app.db.session import get_session
from backend.app.services.knowledge_search import (
    EmbeddingsUnavailable,
    KnowledgeSearchHit,
    search_workspace_knowledge as _run_workspace_knowledge_search,
)


logger = logging.getLogger(__name__)


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


def _source_ref_path(bucket: KnowledgeBucket) -> str | None:
    ref = bucket.source_ref or {}
    if isinstance(ref, dict):
        path = ref.get("path")
        return path if isinstance(path, str) else None
    return None


async def _fetch_body_for_bucket(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    repo: WorkspaceRepo | None,
) -> str | None:
    if repo is None or repo.installation_id is None:
        return None
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        return None
    path = _source_ref_path(bucket)
    if not path:
        return None

    from backend.app.integrations.gateway.code_host import RepoRef
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost

    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        return None

    try:
        gw = GitHubCodeHost(install.installation_id, settings=get_settings())
        blob = await gw.get_blob(
            RepoRef(kind="github", owner=owner, repo=name),
            path=path,
            ref_sha=repo.default_branch or None,
        )
    except Exception:
        return None
    if blob.encoding != "utf-8":
        return None
    return blob.content


def _workspace_bucket_to_dict(bucket: KnowledgeBucket) -> dict[str, Any]:
    updated = bucket.updated_at
    return {
        "slug": bucket.slug,
        "title": bucket.name,
        "visibility": "workspace",
        "repo_id": None,
        "repo_url": None,
        "repo_full_name": None,
        "path": None,
        "size": None,
        "updated_at": updated.isoformat().replace("+00:00", "Z")
        if updated
        else None,
        "excerpt": bucket.description,
        "scope_kind": bucket.scope_kind,
        "source_kind": bucket.source_kind,
        "source_ref": bucket.source_ref,
    }


@router.get("")
async def list_workspace_knowledge(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)

    rows = list(
        (
            await session.execute(
                select(KnowledgeBucket)
                .where(
                    and_(
                        KnowledgeBucket.workspace_id == workspace.id,
                        KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                        KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
                        KnowledgeBucket.archived_at.is_(None),
                    )
                )
                .order_by(KnowledgeBucket.slug)
            )
        )
        .scalars()
        .all()
    )
    buckets = [_workspace_bucket_to_dict(row) for row in rows]
    return {
        "version": 2,
        "workspace_id": str(workspace.id),
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# Workspace vector search
# ---------------------------------------------------------------------------
#
# Registered *before* the ``/{slug}`` detail route so FastAPI's
# first-match-wins dispatch doesn't send a fixed path into the
# repo-files detail handler as ``slug=...``.


class KnowledgeSearchIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    repo_id: uuid.UUID | None = None
    bucket_slug: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=20, ge=1, le=100)


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeSearchHit]


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_workspace_knowledge(
    workspace_id: uuid.UUID,
    payload: KnowledgeSearchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    """Vector search over workspace knowledge."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    try:
        hits = await _run_workspace_knowledge_search(
            session,
            workspace_id=workspace_id,
            query=payload.query,
            repo_id=payload.repo_id,
            bucket_slug=payload.bucket_slug,
            limit=payload.limit,
            settings=get_settings(),
        )
    except EmbeddingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "embeddings_unconfigured",
                "message": str(exc),
            },
        ) from exc

    return KnowledgeSearchResponse(query=payload.query, hits=hits)


# ---------------------------------------------------------------------------
# Per-slug detail (kept last so /search above isn't shadowed by /{slug}).
# ---------------------------------------------------------------------------


@router.get("/{slug}")
async def get_workspace_knowledge(
    workspace_id: uuid.UUID,
    slug: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    workspace = await _load_workspace(session, workspace_id, auth.user.id)

    bucket = (
        await session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
                KnowledgeBucket.slug == slug,
                KnowledgeBucket.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if bucket is not None:
        return _workspace_bucket_to_dict(bucket) | {"body": bucket.description}

    raise HTTPException(
        status_code=404,
        detail=f"knowledge bucket '{slug}' not found",
    )
