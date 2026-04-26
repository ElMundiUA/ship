"""Workspace-level knowledge import sources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    KnowledgeImportSource,
    KnowledgeImportSourceKind,
    KnowledgeIngestionRun,
    KnowledgeSourceItem,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_session
from backend.app.services.knowledge_ingestion import (
    KnowledgeIngestionError,
    create_import_source,
    sync_due_import_sources,
    sync_import_source,
)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/knowledge/sources",
    tags=["knowledge"],
)


class ImportSourceCreateIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    integration_id: uuid.UUID | None = None
    repo_id: uuid.UUID | None = None
    sync_interval_minutes: int | None = Field(default=24 * 60, ge=15)

    @field_validator("kind")
    @classmethod
    def _kind_supported(cls, value: str) -> str:
        clean = value.strip()
        if clean not in KnowledgeImportSourceKind.ALL:
            raise ValueError(f"unsupported source kind: {clean}")
        return clean


class ImportSourceOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    integration_id: uuid.UUID | None
    repo_id: uuid.UUID | None
    kind: str
    name: str
    config: dict[str, Any]
    status: str
    sync_cursor: dict[str, Any] | None
    content_fingerprint: str | None
    sync_interval_minutes: int | None
    last_synced_at: datetime | None
    last_error: str | None
    archived_at: datetime | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SourceItemOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    external_id: str
    title: str
    external_url: str | None
    item_ref: dict[str, Any]
    content_fingerprint: str | None
    cursor: dict[str, Any] | None
    last_seen_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngestionRunOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    trigger: str
    stats: dict[str, Any]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DueSyncOut(BaseModel):
    checked: int
    synced: int
    skipped: int
    errors: int


async def _load_source(
    session: AsyncSession, workspace_id: uuid.UUID, source_id: uuid.UUID
) -> KnowledgeImportSource:
    source = await session.get(KnowledgeImportSource, source_id)
    if source is None or source.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return source


@router.get("", response_model=list[ImportSourceOut])
async def list_import_sources(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ImportSourceOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    background_tasks.add_task(sync_due_import_sources, workspace_id=workspace_id)
    rows = (
        await session.execute(
            select(KnowledgeImportSource)
            .where(
                KnowledgeImportSource.workspace_id == workspace_id,
                KnowledgeImportSource.archived_at.is_(None),
            )
            .order_by(KnowledgeImportSource.created_at.desc())
        )
    ).scalars().all()
    return [_source_out(row) for row in rows]


@router.post("/sync-due", response_model=DueSyncOut)
async def sync_due_sources(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DueSyncOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    result = await sync_due_import_sources(workspace_id=workspace_id, limit=10)
    return DueSyncOut(**result.to_dict())


@router.post("", response_model=ImportSourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    workspace_id: uuid.UUID,
    payload: ImportSourceCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImportSourceOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _validate_source_refs(session, workspace_id, payload)
    try:
        row = await create_import_source(
            session,
            workspace_id=workspace_id,
            kind=payload.kind,
            name=payload.name.strip(),
            config=payload.config,
            integration_id=payload.integration_id,
            repo_id=payload.repo_id,
            actor_user_id=auth.user.id,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
    except KnowledgeIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _source_out(row)


@router.post("/{source_id}/sync", response_model=IngestionRunOut)
async def sync_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> IngestionRunOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    source = await _load_source(session, workspace_id, source_id)
    try:
        run = await sync_import_source(
            session,
            source=source,
            actor_user_id=auth.user.id,
            trigger="manual",
        )
    except KnowledgeIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _run_out(run)


@router.get("/{source_id}/items", response_model=list[SourceItemOut])
async def list_source_items(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[SourceItemOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_source(session, workspace_id, source_id)
    rows = (
        await session.execute(
            select(KnowledgeSourceItem)
            .where(KnowledgeSourceItem.source_id == source_id)
            .order_by(KnowledgeSourceItem.updated_at.desc())
        )
    ).scalars().all()
    return [_item_out(row) for row in rows]


@router.get("/{source_id}/runs", response_model=list[IngestionRunOut])
async def list_source_runs(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[IngestionRunOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_source(session, workspace_id, source_id)
    rows = (
        await session.execute(
            select(KnowledgeIngestionRun)
            .where(KnowledgeIngestionRun.source_id == source_id)
            .order_by(desc(KnowledgeIngestionRun.created_at))
            .limit(25)
        )
    ).scalars().all()
    return [_run_out(row) for row in rows]


async def _validate_source_refs(
    session: AsyncSession, workspace_id: uuid.UUID, payload: ImportSourceCreateIn
) -> None:
    if payload.kind in {
        KnowledgeImportSourceKind.NOTION,
        KnowledgeImportSourceKind.CONFLUENCE,
    }:
        if payload.integration_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{payload.kind} sources require integration_id",
            )
        integration = await session.get(Integration, payload.integration_id)
        if (
            integration is None
            or integration.workspace_id != workspace_id
            or integration.kind != payload.kind
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="integration is not available for this workspace",
            )
    if payload.kind == KnowledgeImportSourceKind.DOCS_REPO:
        if payload.repo_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="docs_repo sources require repo_id",
            )
        repo = await session.get(WorkspaceRepo, payload.repo_id)
        if repo is None or repo.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="repo is not available for this workspace",
            )


def _source_out(row: KnowledgeImportSource) -> ImportSourceOut:
    return ImportSourceOut(
        id=row.id,
        workspace_id=row.workspace_id,
        integration_id=row.integration_id,
        repo_id=row.repo_id,
        kind=row.kind,
        name=row.name,
        config=row.config,
        status=row.status,
        sync_cursor=row.sync_cursor,
        content_fingerprint=row.content_fingerprint,
        sync_interval_minutes=row.sync_interval_minutes,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        archived_at=row.archived_at,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_out(row: KnowledgeSourceItem) -> SourceItemOut:
    return SourceItemOut(
        id=row.id,
        source_id=row.source_id,
        external_id=row.external_id,
        title=row.title,
        external_url=row.external_url,
        item_ref=row.item_ref,
        content_fingerprint=row.content_fingerprint,
        cursor=row.cursor,
        last_seen_at=row.last_seen_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_out(row: KnowledgeIngestionRun) -> IngestionRunOut:
    return IngestionRunOut(
        id=row.id,
        source_id=row.source_id,
        status=row.status,
        trigger=row.trigger,
        stats=row.stats,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
