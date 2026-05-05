"""Workspace-level knowledge import sources."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.db.models.agent_memory import (
    KnowledgeImportSource,
    KnowledgeImportSourceKind,
    KnowledgeIngestionRun,
    KnowledgeSourceItem,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_session
from backend.app.security.encryption import safe_decrypt
from backend.app.services.knowledge_ingestion import (
    KnowledgeIngestionError,
    create_import_source,
    sync_due_import_sources,
    sync_import_source,
)


logger = logging.getLogger(__name__)


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


async def _kick_initial_sync(
    *, workspace_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Run a single ``sync_import_source`` + downstream cascade.

    Lives outside the request session so it runs after the response
    is sent (FastAPI ``BackgroundTasks`` semantics). The cascade step
    (``cascade_workspace_pipeline``) closes the cold-start latency:
    without it, the operator who just connected Notion would wait
    ~30 min for the ``:30`` route tick + ~10 min for the ``:40``
    synth tick before any draft article shows up.

    Failures in either step are swallowed because:

    - sync errors are already persisted on ``source.last_error``;
    - cascade is best-effort — the regular cron tick handles whatever
      the fast-path didn't.
    """
    from backend.app.db.session import get_sessionmaker
    from backend.app.services.knowledge_cascade import (
        cascade_workspace_pipeline,
    )

    sessionmaker = get_sessionmaker()
    sync_ok = False
    async with sessionmaker() as session:
        source = await session.get(KnowledgeImportSource, source_id)
        if source is None or source.workspace_id != workspace_id:
            return
        try:
            await sync_import_source(session, source=source, trigger="create")
            await session.commit()
            sync_ok = True
        except Exception:  # noqa: BLE001 — already persisted on the row
            await session.rollback()
            logger.warning(
                "initial sync failed for source %s — see last_error", source_id
            )
    if sync_ok:
        await cascade_workspace_pipeline(workspace_id=workspace_id)


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
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImportSourceOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _validate_source_refs(session, workspace_id, payload)
    await _reject_duplicate_source(
        session, workspace_id=workspace_id, payload=payload
    )
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
    # Kick the first sync immediately so the operator gets feedback
    # in the UI without having to hit "Sync now". The sync_due cron
    # would otherwise skip a freshly created row (created_at + interval
    # is by definition still in the future), so the next pull would
    # be one full sync_interval_minutes away.
    background_tasks.add_task(
        _kick_initial_sync, workspace_id=workspace_id, source_id=row.id
    )
    return _source_out(row)


@router.post("/{source_id}/sync", response_model=IngestionRunOut)
async def sync_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    background_tasks: BackgroundTasks,
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
    # Run route + synth right after the sync commits so a "Sync now"
    # press surfaces a draft article in seconds, not after the next
    # :30/:40 cron tick. Cascade is idempotent + lock-aware so
    # overlapping with the regular tick is safe.
    from backend.app.services.knowledge_cascade import cascade_workspace_pipeline

    background_tasks.add_task(cascade_workspace_pipeline, workspace_id=workspace_id)
    return _run_out(run)


@router.post("/{source_id}/archive", response_model=ImportSourceOut)
async def archive_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ImportSourceOut:
    """Soft-delete an import source.

    Sets ``archived_at = now`` and stops scheduled syncs by virtue of the
    ``list_import_sources`` filter excluding archived rows. The row itself
    stays so an audit search by source_id still resolves; restoring is a
    DB op for now (no closed-beta operator has asked for a restore button).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    source = await _load_source(session, workspace_id, source_id)

    # Idempotent: a flaky-network retry shouldn't compound the audit trail.
    if source.archived_at is None:
        now = datetime.now(timezone.utc)
        source.archived_at = now
        source.updated_at = now
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=None,
                action="knowledge.import_source.archive",
                target_kind="knowledge_import_source",
                target_id=str(source.id),
                payload={"kind": source.kind, "name": source.name},
            )
        )
        await session.flush()
        await session.refresh(source)

    return _source_out(source)


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


def _canonical_config(config: dict[str, Any] | None) -> str:
    """Order-insensitive JSON form used to detect duplicate sources.

    Two sources are "the same" when they target identical resource_refs
    (and any other config keys) under the same workspace + kind +
    integration + repo. ``resource_refs`` is sorted because operators
    sometimes pick the same pages in a different order in the wizard,
    and that's still a duplicate — there's nothing for the second row
    to do that the first doesn't already cover.

    Other config keys are kept dict-stable via ``sort_keys=True``.
    Non-dict configs collapse to ``{}`` so the check stays defensive.
    """
    if not isinstance(config, dict):
        return "{}"
    refs = config.get("resource_refs")
    other = {k: v for k, v in config.items() if k != "resource_refs"}
    refs_sorted: list[str] = []
    if isinstance(refs, list):
        refs_sorted = sorted(
            json.dumps(r, sort_keys=True, default=str)
            for r in refs
            if isinstance(r, dict)
        )
    return json.dumps(
        {"refs": refs_sorted, "other": other}, sort_keys=True, default=str
    )


async def _reject_duplicate_source(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    payload: ImportSourceCreateIn,
) -> None:
    """409 if a non-archived source with the same canonical config already exists.

    Without this guard a double-click on "Connect" in the wizard creates
    two identical rows; both then sync the same content forever, doubling
    the load on the upstream API and the harvester output, with no upside.
    The check is scoped to ``(workspace, kind, integration, repo)`` plus
    ``archived_at IS NULL`` so re-creating a previously-archived source
    is still allowed (operators sometimes archive then re-add to "reset"
    a source after fixing share permissions upstream).
    """
    target = _canonical_config(payload.config)
    rows = (
        await session.execute(
            select(KnowledgeImportSource).where(
                KnowledgeImportSource.workspace_id == workspace_id,
                KnowledgeImportSource.kind == payload.kind,
                KnowledgeImportSource.integration_id == payload.integration_id,
                KnowledgeImportSource.repo_id == payload.repo_id,
                KnowledgeImportSource.archived_at.is_(None),
            )
        )
    ).scalars().all()
    for row in rows:
        if _canonical_config(row.config) == target:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"a non-archived {payload.kind} source with the same "
                    f"configuration already exists (id={row.id}); archive "
                    "it first or update it instead of creating a duplicate"
                ),
            )


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


# ---------------------------------------------------------------------------
# Connector resource discovery (wizard pickers)
# ---------------------------------------------------------------------------
#
# Until ELS/closed-beta clients had to paste raw {"page_id":"…"} JSON
# into the wizard. They didn't and got a silently-empty sync. These
# endpoints back the picker UI: they list the pages/databases the
# integration's token can actually see, so the wizard can render a
# searchable checklist instead of a JSON textarea.

NOTION_API_ROOT = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
_NOTION_PAGE_SIZE = 50


class NotionResourceItem(BaseModel):
    id: str
    type: Literal["page", "database"]
    title: str
    parent_path: str | None = None
    url: str | None = None
    last_edited_time: str | None = None
    icon: str | None = None  # emoji shorthand or external icon URL


class NotionResourceListOut(BaseModel):
    items: list[NotionResourceItem]
    next_cursor: str | None = None
    has_more: bool = False


def _notion_render_title(rich: list[dict[str, Any]] | None) -> str:
    if not rich:
        return ""
    parts: list[str] = []
    for chunk in rich:
        text = chunk.get("plain_text") or (chunk.get("text") or {}).get("content") or ""
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _notion_extract_title(obj: dict[str, Any]) -> str:
    if obj.get("object") == "database":
        rendered = _notion_render_title(obj.get("title"))
        if rendered:
            return rendered
    props = obj.get("properties") or {}
    for prop in props.values():
        if (prop or {}).get("type") == "title":
            rendered = _notion_render_title(prop.get("title"))
            if rendered:
                return rendered
    return "Untitled"


def _notion_extract_icon(obj: dict[str, Any]) -> str | None:
    icon = obj.get("icon")
    if not isinstance(icon, dict):
        return None
    if icon.get("type") == "emoji":
        return str(icon.get("emoji") or "") or None
    if icon.get("type") == "external":
        return str(((icon.get("external") or {}).get("url")) or "") or None
    if icon.get("type") == "file":
        return str(((icon.get("file") or {}).get("url")) or "") or None
    return None


def _notion_extract_parent_hint(obj: dict[str, Any]) -> str | None:
    parent = obj.get("parent") or {}
    parent_type = parent.get("type")
    if parent_type == "workspace":
        return "Workspace"
    if parent_type == "database_id":
        return "Database"
    if parent_type == "page_id":
        return "Page"
    if parent_type == "block_id":
        return "Block"
    return None


async def _notion_search(
    *, token: str, body: dict[str, Any], workspace_id: uuid.UUID
) -> dict[str, Any]:
    """POST to Notion /search with auth + version headers.

    Extracted so tests can monkeypatch this single function (mirrors the
    seam used by ``notion_oauth.exchange_code_for_token``) instead of
    fighting ``httpx.MockTransport``.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                f"{NOTION_API_ROOT}/search", json=body, headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("notion /search transport error for ws=%s: %s", workspace_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="notion API is unreachable — try again",
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="notion rejected the integration token — reconnect Notion",
        )
    if response.status_code >= 400:
        logger.warning(
            "notion /search returned HTTP %s for ws=%s: %s",
            response.status_code,
            workspace_id,
            response.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"notion returned HTTP {response.status_code}",
        )

    return response.json()


@router.get("/notion/resources", response_model=NotionResourceListOut)
async def list_notion_resources(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID = Query(..., description="Notion integration row to query"),
    q: str = Query("", description="Free-text search query forwarded to Notion"),
    cursor: str | None = Query(default=None, description="Notion start_cursor for pagination"),
    type_filter: Literal["page", "database", "any"] = Query(
        default="page", alias="type", description="Restrict results to pages, databases, or both"
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NotionResourceListOut:
    """Proxy Notion's /search so the wizard can render a real picker.

    Restricts ``type_filter`` to ``page`` by default because the
    Notion connector's v1 fetcher (``backend/app/services/connectors/
    notion.py``) only handles ``{"page_id": ...}`` refs — letting users
    pick databases would just round-trip into ``ConnectorUnsupported``
    fallback. ``type=any`` exists for forward compatibility once the
    fetcher learns databases.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    integration = await session.get(Integration, integration_id)
    if (
        integration is None
        or integration.workspace_id != workspace_id
        or integration.kind != "notion"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notion integration not found for this workspace",
        )

    token = safe_decrypt(integration.secret_ciphertext)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="notion integration has no readable access token — reconnect it",
        )

    body: dict[str, Any] = {
        "page_size": _NOTION_PAGE_SIZE,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    }
    if q.strip():
        body["query"] = q.strip()
    if type_filter in ("page", "database"):
        body["filter"] = {"value": type_filter, "property": "object"}
    if cursor:
        body["start_cursor"] = cursor

    payload = await _notion_search(token=token, body=body, workspace_id=workspace_id)
    raw_results = payload.get("results") or []
    items: list[NotionResourceItem] = []
    for obj in raw_results:
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("object")
        if obj_type not in ("page", "database"):
            continue
        items.append(
            NotionResourceItem(
                id=str(obj.get("id") or ""),
                type=obj_type,
                title=_notion_extract_title(obj),
                parent_path=_notion_extract_parent_hint(obj),
                url=str(obj.get("url") or "") or None,
                last_edited_time=str(obj.get("last_edited_time") or "") or None,
                icon=_notion_extract_icon(obj),
            )
        )

    return NotionResourceListOut(
        items=items,
        next_cursor=payload.get("next_cursor"),
        has_more=bool(payload.get("has_more")),
    )


# ---------------------------------------------------------------------------
# Confluence picker
# ---------------------------------------------------------------------------

_CONFLUENCE_PAGE_SIZE = 25


class ConfluenceResourceItem(BaseModel):
    id: str
    title: str
    space_key: str | None = None
    space_name: str | None = None
    url: str | None = None
    last_edited_time: str | None = None


class ConfluenceResourceListOut(BaseModel):
    items: list[ConfluenceResourceItem]
    next_start: int | None = None  # offset for the next page
    has_more: bool = False


def _confluence_site_creds(integration: Integration) -> tuple[str, str, str]:
    """Return (site_url, email, api_token) or raise HTTPException."""
    token = safe_decrypt(integration.secret_ciphertext)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confluence integration has no readable API token — reconnect it",
        )
    config = integration.config or {}
    site_url = str(config.get("site_url") or config.get("site") or "").strip().rstrip("/")
    email = str(config.get("email") or config.get("user") or "").strip()
    if not site_url or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confluence integration is missing site_url/email — reconnect it",
        )
    if not site_url.startswith("http"):
        site_url = f"https://{site_url}"
    return site_url, email, token


async def _confluence_search(
    *,
    site_url: str,
    email: str,
    token: str,
    cql: str,
    start: int,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """GET /wiki/rest/api/content/search with CQL.

    Confluence v2 REST has no full-text search, only paginated lists by
    space. The v1 ``/content/search`` endpoint with CQL is the supported
    way to do typeahead. Same Basic-auth shape as the connector fetcher.
    """
    params = {
        "cql": cql,
        "limit": _CONFLUENCE_PAGE_SIZE,
        "start": start,
        "expand": "space,history.lastUpdated",
    }
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                f"{site_url}/wiki/rest/api/content/search",
                params=params,
                headers=headers,
                auth=(email, token),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "confluence /content/search transport error for ws=%s: %s",
            workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="confluence is unreachable — try again",
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confluence rejected the API token — reconnect Confluence",
        )
    if response.status_code >= 400:
        logger.warning(
            "confluence /content/search returned HTTP %s for ws=%s: %s",
            response.status_code,
            workspace_id,
            response.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"confluence returned HTTP {response.status_code}",
        )
    return response.json()


def _build_confluence_cql(query: str, space_key: str | None) -> str:
    """Build a CQL clause for the picker.

    Always restricts to ``type = "page"`` because the connector fetcher
    only supports page refs. ``query`` and ``space_key`` are optional
    narrowers; both are CQL-quoted to avoid injection of operators.
    """
    parts = ['type = "page"']
    if space_key:
        parts.append(f'space.key = "{_cql_escape(space_key)}"')
    cleaned = query.strip()
    if cleaned:
        parts.append(f'text ~ "{_cql_escape(cleaned)}"')
    return " AND ".join(parts) + ' ORDER BY lastModified DESC'


def _cql_escape(value: str) -> str:
    # CQL strings are double-quoted; escape backslashes and quotes only.
    return value.replace("\\", "\\\\").replace('"', '\\"')


# --- Space + section listing (the picker primitives) ---


class ConfluenceSpaceItem(BaseModel):
    id: str
    key: str
    name: str
    type: str | None = None  # global, personal, etc.
    homepage_id: str | None = None
    description: str | None = None


class ConfluenceSpaceListOut(BaseModel):
    items: list[ConfluenceSpaceItem]


class ConfluenceSectionItem(BaseModel):
    """A top-level page in a space — the natural "section" unit operators
    pick to ingest a chunk of docs (root + descendants) at once.
    """

    id: str
    title: str
    space_id: str
    space_key: str | None = None
    space_name: str | None = None
    url: str | None = None
    last_edited_time: str | None = None
    has_children: bool = False


class ConfluenceSectionListOut(BaseModel):
    items: list[ConfluenceSectionItem]
    next_cursor: str | None = None
    has_more: bool = False


async def _confluence_get(
    *,
    site_url: str,
    email: str,
    token: str,
    path: str,
    params: dict[str, Any],
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                f"{site_url}{path}",
                params={k: v for k, v in params.items() if v is not None},
                headers=headers,
                auth=(email, token),
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "confluence GET %s transport error for ws=%s: %s",
            path,
            workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="confluence is unreachable — try again",
        ) from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confluence rejected the API token — reconnect Confluence",
        )
    if response.status_code >= 400:
        logger.warning(
            "confluence GET %s returned HTTP %s for ws=%s: %s",
            path,
            response.status_code,
            workspace_id,
            response.text[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"confluence returned HTTP {response.status_code}",
        )
    return response.json()


@router.get("/confluence/spaces", response_model=ConfluenceSpaceListOut)
async def list_confluence_spaces(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID = Query(..., description="Confluence integration row to query"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ConfluenceSpaceListOut:
    """List Confluence spaces visible to the integration's API token."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    integration = await session.get(Integration, integration_id)
    if (
        integration is None
        or integration.workspace_id != workspace_id
        or integration.kind != "confluence"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="confluence integration not found for this workspace",
        )

    site_url, email, token = _confluence_site_creds(integration)
    payload = await _confluence_get(
        site_url=site_url,
        email=email,
        token=token,
        path="/wiki/api/v2/spaces",
        params={"limit": 100, "description-format": "plain"},
        workspace_id=workspace_id,
    )

    items: list[ConfluenceSpaceItem] = []
    for obj in payload.get("results") or []:
        if not isinstance(obj, dict):
            continue
        items.append(
            ConfluenceSpaceItem(
                id=str(obj.get("id") or ""),
                key=str(obj.get("key") or ""),
                name=str(obj.get("name") or "Untitled space"),
                type=str(obj.get("type") or "") or None,
                homepage_id=str(obj.get("homepageId") or "") or None,
                description=_confluence_extract_description(obj.get("description")),
            )
        )
    return ConfluenceSpaceListOut(items=items)


def _confluence_extract_description(raw: Any) -> str | None:
    """v2 spaces returns description as ``{plain: {value: "..."}}`` or null."""
    if not isinstance(raw, dict):
        return None
    plain = raw.get("plain")
    if isinstance(plain, dict):
        value = str(plain.get("value") or "").strip()
        return value or None
    return None


@router.get("/confluence/sections", response_model=ConfluenceSectionListOut)
async def list_confluence_sections(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID = Query(..., description="Confluence integration row to query"),
    space_id: str = Query(..., description="Confluence space id to list sections from"),
    cursor: str | None = Query(default=None, description="Confluence pagination cursor"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ConfluenceSectionListOut:
    """Top-level pages (= sections) of a space.

    A "section" is the natural ingestion unit — usually a chapter/handbook
    rooted at a top-level page. Selecting a section ingests its root
    page plus every descendant.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    integration = await session.get(Integration, integration_id)
    if (
        integration is None
        or integration.workspace_id != workspace_id
        or integration.kind != "confluence"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="confluence integration not found for this workspace",
        )

    site_url, email, token = _confluence_site_creds(integration)

    # Resolve space metadata once so we can stamp it on each section item
    # (saves the picker UI from a second lookup; one space = one cheap call).
    try:
        space_payload = await _confluence_get(
            site_url=site_url,
            email=email,
            token=token,
            path=f"/wiki/api/v2/spaces/{space_id}",
            params={},
            workspace_id=workspace_id,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_502_BAD_GATEWAY:
            space_payload = {}
        else:
            raise
    space_key = str(space_payload.get("key") or "") or None
    space_name = str(space_payload.get("name") or "") or None

    payload = await _confluence_get(
        site_url=site_url,
        email=email,
        token=token,
        path="/wiki/api/v2/pages",
        params={
            "space-id": space_id,
            "limit": 50,
            "depth": "root",  # only top-level pages
            "cursor": cursor,
            "body-format": None,  # we don't need bodies in the picker
        },
        workspace_id=workspace_id,
    )

    items: list[ConfluenceSectionItem] = []
    for obj in payload.get("results") or []:
        if not isinstance(obj, dict):
            continue
        web_url = ((obj.get("_links") or {}).get("webui")) if isinstance(obj.get("_links"), dict) else None
        absolute_url = f"{site_url}/wiki{web_url}" if isinstance(web_url, str) and web_url.startswith("/") else web_url
        version = obj.get("version") if isinstance(obj.get("version"), dict) else {}
        items.append(
            ConfluenceSectionItem(
                id=str(obj.get("id") or ""),
                title=str(obj.get("title") or "Untitled"),
                space_id=str(obj.get("spaceId") or space_id),
                space_key=space_key,
                space_name=space_name,
                url=absolute_url,
                last_edited_time=str(version.get("createdAt") or "") or None,
            )
        )

    next_cursor = _extract_confluence_cursor(payload)
    return ConfluenceSectionListOut(
        items=items,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


def _extract_confluence_cursor(payload: dict[str, Any]) -> str | None:
    """v2 paginated responses include _links.next as a relative path with
    cursor=… in the query string. Parse it out so callers can pass it back."""
    links = payload.get("_links") if isinstance(payload.get("_links"), dict) else {}
    next_link = links.get("next")
    if not isinstance(next_link, str) or not next_link:
        return None
    # The link looks like "/wiki/api/v2/pages?space-id=...&cursor=eyJ..."
    if "cursor=" not in next_link:
        return None
    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(next_link)
        cursor_values = parse_qs(parsed.query).get("cursor")
        if cursor_values:
            return cursor_values[0]
    except Exception:  # noqa: BLE001
        return None
    return None


# --- CQL-based page search (kept as advanced-mode hook; not exposed in UI v1) ---


@router.get("/confluence/resources", response_model=ConfluenceResourceListOut)
async def list_confluence_resources(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID = Query(..., description="Confluence integration row to query"),
    q: str = Query("", description="Free-text query forwarded to Confluence via CQL text~"),
    space_key: str | None = Query(default=None, description="Optional space key narrower"),
    start: int = Query(default=0, ge=0, description="Offset into the Confluence result set"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ConfluenceResourceListOut:
    """Page-level CQL search. The picker UI ships space-first; this stays
    available for power-user / API callers who want to pick specific pages."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    integration = await session.get(Integration, integration_id)
    if (
        integration is None
        or integration.workspace_id != workspace_id
        or integration.kind != "confluence"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="confluence integration not found for this workspace",
        )

    site_url, email, token = _confluence_site_creds(integration)
    cql = _build_confluence_cql(q, space_key)
    payload = await _confluence_search(
        site_url=site_url,
        email=email,
        token=token,
        cql=cql,
        start=start,
        workspace_id=workspace_id,
    )

    raw_results = payload.get("results") or []
    items: list[ConfluenceResourceItem] = []
    for obj in raw_results:
        if not isinstance(obj, dict):
            continue
        space = obj.get("space") if isinstance(obj.get("space"), dict) else {}
        history = obj.get("history") if isinstance(obj.get("history"), dict) else {}
        last_updated = (history.get("lastUpdated") or {}).get("when") if isinstance(history.get("lastUpdated"), dict) else None
        web_url = ((obj.get("_links") or {}).get("webui")) if isinstance(obj.get("_links"), dict) else None
        absolute_url = f"{site_url}/wiki{web_url}" if isinstance(web_url, str) and web_url.startswith("/") else web_url
        items.append(
            ConfluenceResourceItem(
                id=str(obj.get("id") or ""),
                title=str(obj.get("title") or "Untitled"),
                space_key=str(space.get("key") or "") or None,
                space_name=str(space.get("name") or "") or None,
                url=absolute_url,
                last_edited_time=str(last_updated) if last_updated else None,
            )
        )

    size = int(payload.get("size") or len(raw_results))
    limit = int(payload.get("limit") or _CONFLUENCE_PAGE_SIZE)
    next_start_value = start + size if size >= limit else None
    return ConfluenceResourceListOut(
        items=items,
        next_start=next_start_value,
        has_more=next_start_value is not None,
    )


# ---------------------------------------------------------------------------
# Docs-repo file-tree picker
# ---------------------------------------------------------------------------
#
# The wizard's docs_repo source previously asked operators to type path
# prefixes by hand into a textarea (default: ``docs\nREADME.md``). This
# endpoint surfaces the actual file tree so operators check folders/files
# instead of guessing names. Resource refs end up in ``config.paths`` —
# same shape the connector already accepts (any path that startswith
# any prefix in ``paths`` and matches an extension is ingested), so a
# folder pick = "include this folder recursively" without any backend
# behaviour change.

_DOCS_REPO_DEFAULT_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".adoc",
)


class DocsRepoTreeNode(BaseModel):
    path: str
    name: str
    type: Literal["tree", "blob"]
    matches_extension: bool  # blobs only; trees pass through as True


class DocsRepoTreeOut(BaseModel):
    repo_id: uuid.UUID
    ref: str
    truncated: bool  # tree exceeded GitHub's 100k-entry / 7MB cap
    nodes: list[DocsRepoTreeNode]


def _node_for_blob(path: str, *, extensions: tuple[str, ...]) -> DocsRepoTreeNode:
    return DocsRepoTreeNode(
        path=path,
        name=path.rsplit("/", 1)[-1],
        type="blob",
        matches_extension=path.lower().endswith(extensions),
    )


def _node_for_tree(path: str) -> DocsRepoTreeNode:
    return DocsRepoTreeNode(
        path=path,
        name=path.rsplit("/", 1)[-1] if "/" in path else path,
        type="tree",
        matches_extension=True,
    )


@router.get("/docs-repo/tree", response_model=DocsRepoTreeOut)
async def list_docs_repo_tree(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID = Query(..., description="Workspace repo to list"),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DocsRepoTreeOut:
    """Flat tree (folders + doc files) for the wizard's docs-repo picker.

    Uses GitHub's recursive Trees API so we get the whole tree in one
    call (capped server-side at 100k entries / 7MB by GitHub itself —
    surfaced to the client via ``truncated``). The frontend builds the
    expandable tree view from the flat list; we don't expose per-folder
    pagination because GitHub's recursive=1 already returns everything
    cheaply for the repos closed-beta operators ingest.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo = await session.get(WorkspaceRepo, repo_id)
    if repo is None or repo.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repo not found for this workspace",
        )
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo has no GitHub installation — re-authorize Ship for this org",
        )
    installation = await session.get(GitHubInstallation, repo.installation_id)
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub installation row is missing",
        )

    owner, name = repo.full_name.split("/", 1)
    ref = RepoRef(kind="github", owner=owner, repo=name)
    gateway = GitHubCodeHost(installation.installation_id, settings=settings)

    try:
        tree_payload = await _docs_repo_fetch_tree(
            gateway,
            owner=owner,
            repo=name,
            ref=repo.default_branch or "HEAD",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "github trees fetch failed for repo=%s ws=%s: %s",
            repo.full_name,
            workspace_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub is unreachable — try again",
        ) from exc

    extensions = tuple(s.lower() for s in _DOCS_REPO_DEFAULT_EXTENSIONS)
    blob_paths = sorted(
        path for path in tree_payload["blobs"] if path.lower().endswith(extensions)
    )
    tree_paths = sorted(
        path
        for path in tree_payload["trees"]
        # Drop folders that have no doc descendants; otherwise the picker
        # is full of empty folders the operator can never use.
        if any(blob.startswith(f"{path}/") for blob in blob_paths)
    )
    nodes = [_node_for_tree(p) for p in tree_paths] + [
        _node_for_blob(p, extensions=extensions) for p in blob_paths
    ]

    return DocsRepoTreeOut(
        repo_id=repo.id,
        ref=tree_payload["ref"],
        truncated=bool(tree_payload["truncated"]),
        nodes=nodes,
    )


async def _docs_repo_fetch_tree(
    gateway: GitHubCodeHost, *, owner: str, repo: str, ref: str
) -> dict[str, Any]:
    """Hit GitHub's recursive trees API and split blobs/trees.

    Wrapped so tests can monkeypatch the GitHub round-trip without
    touching ``GitHubCodeHost.list_files`` (which has slightly different
    semantics — it drops trees and only returns blob paths).
    """
    response = await gateway._request(  # type: ignore[attr-defined]
        "GET",
        f"/repos/{owner}/{repo}/git/trees/{ref}",
        params={"recursive": "1"},
    )
    payload = response.json()
    blobs: list[str] = []
    trees: list[str] = []
    for entry in payload.get("tree") or []:
        path = entry.get("path")
        kind = entry.get("type")
        if not path or not kind:
            continue
        if kind == "blob":
            blobs.append(str(path))
        elif kind == "tree":
            trees.append(str(path))
    return {
        "ref": str(payload.get("sha") or ref),
        "blobs": blobs,
        "trees": trees,
        "truncated": bool(payload.get("truncated")),
    }


# ---------------------------------------------------------------------------
# Website preview (Firecrawl /map proxy)
# ---------------------------------------------------------------------------
#
# Operators creating a website source guess at what URLs Firecrawl will
# discover. Surfacing the /map result before they hit "Create" turns the
# guess into a confirmation and lets them tune limit/scope without a
# create-sync-archive cycle.


class WebsitePreviewOut(BaseModel):
    urls: list[str]
    truncated: bool  # we hit the requested limit; there may be more


@router.get("/website/preview", response_model=WebsitePreviewOut)
async def preview_website_crawl(
    workspace_id: uuid.UUID,
    url: str = Query(..., description="Root URL to map"),
    limit: int = Query(default=25, ge=1, le=200),
    include_subdomains: bool = Query(default=False),
    auth: AuthContext = Depends(get_current_auth),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> WebsitePreviewOut:
    """Run Firecrawl ``/map`` against ``url`` and return discovered links.

    No persistence — purely a "what would happen" probe. Same FIRECRAWL_API_KEY
    used by the sync path. Operator must be a workspace admin (consistent
    with create_source) so we don't expose Firecrawl as a public probe.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if not settings.firecrawl_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firecrawl is not configured on this deployment",
        )

    from backend.app.services.knowledge_ingestion import _firecrawl_map

    cleaned = url.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="url is required",
        )

    try:
        urls = await _firecrawl_map(
            cleaned,
            config={"limit": limit, "include_subdomains": include_subdomains},
            settings=settings,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "firecrawl /map preview failed for ws=%s url=%s: %s",
            workspace_id,
            cleaned,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Firecrawl could not map this URL — check that it's reachable",
        ) from exc

    truncated = len(urls) >= limit
    return WebsitePreviewOut(urls=urls, truncated=truncated)
