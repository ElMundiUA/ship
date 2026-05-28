"""Workspace-scoped DB knowledge buckets."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_MAINTAIN,
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
from backend.app.integrations.lighthouse import build_lighthouse_client
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
# Lighthouse corpus overview (K7) — what the per-workspace engine actually
# holds, for the knowledge dashboard. Kept above /{slug} so "corpus" isn't
# captured as a slug.
# ---------------------------------------------------------------------------


class KnowledgeCorpusSourceOut(BaseModel):
    source: str
    chunk_count: int
    recipes: list[str] = Field(default_factory=list)
    last_ingest_at: str | None = None


class KnowledgeCorpusOut(BaseModel):
    """Lighthouse corpus roll-up for the workspace. ``configured`` is
    False when Lighthouse isn't wired (``LIGHTHOUSE_BASE_URL`` unset) or
    unreachable — the dashboard then shows an "engine not connected"
    state instead of erroring."""

    configured: bool
    total_chunks: int = 0
    total_sources: int = 0
    last_ingest_at: str | None = None
    sources: list[KnowledgeCorpusSourceOut] = Field(default_factory=list)


@router.get("/corpus", response_model=KnowledgeCorpusOut)
async def get_workspace_corpus(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeCorpusOut:
    """Lighthouse corpus stats + per-source roll-up for the workspace."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    client = build_lighthouse_client(get_settings())
    if client is None:
        return KnowledgeCorpusOut(configured=False)
    try:
        stats = await client.corpus_stats(workspace_id=workspace_id)
        raw_sources = await client.corpus_sources(workspace_id=workspace_id)
    except Exception:
        logger.warning(
            "lighthouse corpus fetch failed for ws=%s", workspace_id,
            exc_info=True,
        )
        return KnowledgeCorpusOut(configured=False)

    return KnowledgeCorpusOut(
        configured=True,
        total_chunks=int(stats.get("total_chunks") or 0),
        total_sources=int(stats.get("total_sources") or 0),
        last_ingest_at=stats.get("last_ingest_at"),
        sources=[
            KnowledgeCorpusSourceOut(
                source=str(s.get("source") or ""),
                chunk_count=int(s.get("chunk_count") or 0),
                recipes=list(s.get("recipes") or []),
                last_ingest_at=s.get("last_ingest_at"),
            )
            for s in raw_sources
        ],
    )


# ---------------------------------------------------------------------------
# Workspace importer CRUD — operator-driven "Add import source" surface.
# Proxies Lighthouse's admin importer API, scoped to the workspace.
# Kept above /{slug} so "importers" isn't captured as a slug.
# ---------------------------------------------------------------------------


class ImporterTypeOut(BaseModel):
    type: str
    display_name: str
    description: str
    config_schema: dict[str, Any] = Field(default_factory=dict)
    secret_keys: list[str] = Field(default_factory=list)
    supports_discovery: bool = False
    discovery_required: list[str] = Field(default_factory=list)


class ImporterOut(BaseModel):
    id: str
    type: str
    name: str
    description: str | None = None
    recipe: str
    config: dict[str, Any] = Field(default_factory=dict)
    has_secrets: bool = False
    enabled: bool = True
    status: str
    last_run_at: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ImporterCreateIn(BaseModel):
    type: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    recipe: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


def _coerce_importer(row: dict[str, Any]) -> ImporterOut:
    return ImporterOut(
        id=str(row.get("id") or ""),
        type=str(row.get("type") or ""),
        name=str(row.get("name") or ""),
        description=row.get("description"),
        recipe=str(row.get("recipe") or ""),
        config=dict(row.get("config") or {}),
        has_secrets=bool(row.get("has_secrets")),
        enabled=bool(row.get("enabled", True)),
        status=str(row.get("status") or "idle"),
        last_run_at=row.get("last_run_at"),
        last_error=row.get("last_error"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _require_lighthouse_or_503():
    client = build_lighthouse_client(get_settings())
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "lighthouse_unconfigured",
                "message": "Lighthouse engine is not configured.",
            },
        )
    return client


@router.get("/importers/types", response_model=list[ImporterTypeOut])
async def list_importer_types(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ImporterTypeOut]:
    """All importer types the engine knows about, with config schemas."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    client = _require_lighthouse_or_503()
    try:
        rows = await client.list_importer_types()
    except Exception as exc:
        logger.warning("lighthouse types fetch failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="lighthouse unavailable",
        ) from exc
    return [
        ImporterTypeOut(
            type=str(r.get("type") or ""),
            display_name=str(r.get("display_name") or ""),
            description=str(r.get("description") or ""),
            config_schema=dict(r.get("config_schema") or {}),
            secret_keys=list(r.get("secret_keys") or []),
            supports_discovery=bool(r.get("supports_discovery")),
            discovery_required=list(r.get("discovery_required") or []),
        )
        for r in rows
    ]


@router.get("/importers", response_model=list[ImporterOut])
async def list_workspace_importers(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ImporterOut]:
    """List every importer registered against this workspace."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    client = _require_lighthouse_or_503()
    try:
        rows = await client.list_workspace_importers(workspace_id=workspace_id)
    except Exception as exc:
        logger.warning("lighthouse list importers failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="lighthouse unavailable",
        ) from exc
    return [_coerce_importer(r) for r in rows]


@router.post(
    "/importers",
    response_model=ImporterOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_importer(
    workspace_id: uuid.UUID,
    payload: ImporterCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImporterOut:
    """Create a new importer in this workspace. ``recipe`` defaults to
    ``workspace-<type>`` if not supplied — it's just a grouping label."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_MAINTAIN)
    client = _require_lighthouse_or_503()
    recipe = payload.recipe or f"workspace-{payload.type}"
    try:
        row = await client.create_importer(
            workspace_id=workspace_id,
            type_=payload.type,
            name=payload.name,
            description=payload.description,
            recipe=recipe,
            config=payload.config,
            secrets=payload.secrets,
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        logger.warning("lighthouse create importer rejected: %s", body)
        # Forward the engine's validation error to the operator.
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=body,
        ) from exc
    except Exception as exc:
        logger.warning("lighthouse create importer failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="lighthouse unavailable",
        ) from exc
    return _coerce_importer(row)


class ImporterDiscoverIn(BaseModel):
    type: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ImporterDiscoveredItem(BaseModel):
    id: str
    name: str
    kind: str
    hint: str | None = None
    config_patch: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/importers/discover",
    response_model=list[ImporterDiscoveredItem],
)
async def discover_importer_items(
    workspace_id: uuid.UUID,
    payload: ImporterDiscoverIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ImporterDiscoveredItem]:
    """Probe the source with the given config/secrets and return the
    items the operator can choose from. No DB writes."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_MAINTAIN)
    client = _require_lighthouse_or_503()
    try:
        items = await client.discover_importer(
            workspace_id=workspace_id,
            type_=payload.type,
            config=payload.config,
            secrets=payload.secrets,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc
    except Exception as exc:
        logger.warning("lighthouse discover failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="lighthouse unavailable",
        ) from exc
    return [
        ImporterDiscoveredItem(
            id=str(i.get("id") or ""),
            name=str(i.get("name") or ""),
            kind=str(i.get("kind") or ""),
            hint=i.get("hint"),
            config_patch=dict(i.get("config_patch") or {}),
        )
        for i in items
    ]


@router.post(
    "/importers/{importer_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_workspace_importer(
    workspace_id: uuid.UUID,
    importer_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Trigger an on-demand run of the importer (background)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_MAINTAIN)
    client = _require_lighthouse_or_503()
    try:
        return await client.run_importer(
            workspace_id=workspace_id, importer_id=importer_id
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc
    except Exception as exc:
        logger.warning("lighthouse run importer failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="lighthouse unavailable",
        ) from exc


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
