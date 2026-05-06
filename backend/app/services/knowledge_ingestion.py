"""Workspace-level knowledge source ingestion."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    KnowledgeImportSource,
    KnowledgeImportSourceKind,
    KnowledgeIngestionRun,
    KnowledgeIngestionStatus,
    KnowledgeSourceItem,
    KnowledgeSourceStatus,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.connectors import fetch_connector_pages


# Bumped from 100 → 5000 with the claim-graph pipeline. The legacy
# cap was sized to the old synth shape where 100 docs already
# pushed an LLM batch hard; the new pipeline batches at the
# extractor tick (25 items / 20 min) so the per-source ceiling can
# sit far above realistic doc counts (Ship's repo: ~150-300
# markdown, Notion workspaces typically <2000 pages). 5000 still
# catches pathological cases without forcing operators to manually
# chunk their sources.
MAX_SOURCE_DOCUMENTS = 5000
MAX_SECTION_CHARS = 18_000
SYNC_DUE_LIMIT = 5


class KnowledgeIngestionError(RuntimeError):
    """Base error for workspace source ingestion failures."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    external_id: str
    title: str
    body_md: str
    external_url: str | None = None
    item_ref: dict[str, Any] | None = None
    cursor: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class IngestionStats:
    discovered: int = 0
    changed: int = 0
    skipped: int = 0
    sections: int = 0
    notes_created: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "changed": self.changed,
            "skipped": self.skipped,
            "sections": self.sections,
            "notes_created": self.notes_created,
            "errors": self.errors,
        }


@dataclass(frozen=True, slots=True)
class DueSyncResult:
    checked: int = 0
    synced: int = 0
    skipped: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "synced": self.synced,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_text(raw)


def _normalise_markdown(value: str) -> str:
    lines = [line.rstrip() for line in (value or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _split_sections(doc: SourceDocument) -> list[SourceDocument]:
    body = _normalise_markdown(doc.body_md)
    if not body:
        return []
    if len(body) <= MAX_SECTION_CHARS:
        return [doc]
    sections: list[SourceDocument] = []
    title = doc.title
    lines: list[str] = []
    for line in body.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading and lines:
            sections.append(_section_doc(doc, title, lines, len(sections) + 1))
            title = heading.group(1).strip()[:512] or doc.title
            lines = [line]
        else:
            lines.append(line)
    if lines:
        sections.append(_section_doc(doc, title, lines, len(sections) + 1))
    return [section for section in sections if section.body_md] or [doc]


def _section_doc(
    doc: SourceDocument, title: str, lines: list[str], index: int
) -> SourceDocument:
    return SourceDocument(
        external_id=f"{doc.external_id}#section-{index}",
        title=title,
        body_md=_normalise_markdown("\n".join(lines)),
        external_url=doc.external_url,
        item_ref={**(doc.item_ref or {}), "section_index": index},
        cursor=doc.cursor,
    )


async def create_import_source(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    name: str,
    config: dict[str, Any],
    integration_id: uuid.UUID | None = None,
    repo_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    sync_interval_minutes: int | None = 24 * 60,
) -> KnowledgeImportSource:
    if kind not in KnowledgeImportSourceKind.ALL:
        raise KnowledgeIngestionError(f"unsupported source kind: {kind}")
    row = KnowledgeImportSource(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        name=name,
        config=config,
        integration_id=integration_id,
        repo_id=repo_id,
        created_by_user_id=actor_user_id,
        sync_interval_minutes=sync_interval_minutes,
    )
    session.add(row)
    await session.flush()
    return row


async def sync_import_source(
    session: AsyncSession,
    *,
    source: KnowledgeImportSource,
    actor_user_id: uuid.UUID | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
) -> KnowledgeIngestionRun:
    settings = settings or get_settings()
    run = KnowledgeIngestionRun(
        id=uuid.uuid4(),
        workspace_id=source.workspace_id,
        source_id=source.id,
        status=KnowledgeIngestionStatus.RUNNING,
        trigger=trigger,
        stats={},
        started_at=datetime.now(timezone.utc),
        created_by_user_id=actor_user_id,
    )
    session.add(run)
    source.status = KnowledgeSourceStatus.SYNCING
    source.last_error = None
    await session.flush()
    try:
        documents = await fetch_source_documents(session, source, settings=settings)
        stats = await _ingest_documents(
            session,
            source=source,
            run=run,
            documents=documents[:MAX_SOURCE_DOCUMENTS],
            actor_user_id=actor_user_id,
            settings=settings,
        )
        source.status = KnowledgeSourceStatus.READY
        source.last_synced_at = datetime.now(timezone.utc)
        source.last_error = None
        source.content_fingerprint = _stable_hash(
            [
                {
                    "external_id": doc.external_id,
                    "fingerprint": _sha256_text(_normalise_markdown(doc.body_md)),
                }
                for doc in documents
            ]
        )
        run.status = KnowledgeIngestionStatus.DONE
        run.stats = stats.to_dict()
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        await session.refresh(run)
        return run
    except Exception as exc:
        source.status = KnowledgeSourceStatus.ERROR
        source.last_error = f"{type(exc).__name__}: {exc}"[:4000]
        run.status = KnowledgeIngestionStatus.ERROR
        run.error = source.last_error
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        raise


async def sync_due_import_sources(
    *,
    workspace_id: uuid.UUID | None = None,
    limit: int = SYNC_DUE_LIMIT,
    settings: Settings | None = None,
) -> DueSyncResult:
    """Sync due sources from the API process, without an external worker.

    This is intentionally bounded and opens its own session so it can be used
    from FastAPI BackgroundTasks or an HTTP cron endpoint after the request
    session has been returned.
    """

    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    sessionmaker = get_sessionmaker()
    checked = synced = skipped = errors = 0
    async with sessionmaker() as session:
        stmt = (
            select(KnowledgeImportSource)
            .where(
                KnowledgeImportSource.archived_at.is_(None),
                ~KnowledgeImportSource.status.in_(
                    [KnowledgeSourceStatus.DISABLED, KnowledgeSourceStatus.SYNCING]
                ),
                KnowledgeImportSource.sync_interval_minutes.is_not(None),
            )
            .order_by(KnowledgeImportSource.updated_at.asc())
            .limit(max(1, min(limit, 25)))
        )
        if workspace_id is not None:
            stmt = stmt.where(KnowledgeImportSource.workspace_id == workspace_id)
        rows = (await session.execute(stmt)).scalars().all()

        for source in rows:
            checked += 1
            interval = source.sync_interval_minutes or 0
            if interval <= 0:
                skipped += 1
                continue
            due_at = (source.last_synced_at or source.created_at) + timedelta(
                minutes=interval
            )
            if due_at > now:
                skipped += 1
                continue
            try:
                await sync_import_source(
                    session,
                    source=source,
                    trigger="scheduled",
                    settings=settings,
                )
                await session.commit()
                synced += 1
            except Exception:  # noqa: BLE001
                await session.rollback()
                errors += 1
    return DueSyncResult(
        checked=checked,
        synced=synced,
        skipped=skipped,
        errors=errors,
    )


async def fetch_source_documents(
    session: AsyncSession,
    source: KnowledgeImportSource,
    *,
    settings: Settings,
) -> list[SourceDocument]:
    if source.kind in {KnowledgeImportSourceKind.NOTION, KnowledgeImportSourceKind.CONFLUENCE}:
        return await _fetch_connector_documents(session, source)
    if (
        source.kind == KnowledgeImportSourceKind.DOCS_REPO
        and source.config.get("harvester") == "repo_intel"
    ):
        return []
    if source.kind == KnowledgeImportSourceKind.WEBSITE:
        return await _fetch_firecrawl_documents(source, settings=settings)
    if source.kind == KnowledgeImportSourceKind.STATIC_UPLOAD:
        return _fetch_static_documents(source)
    if source.kind == KnowledgeImportSourceKind.DOCS_REPO:
        return await _fetch_docs_repo_documents(session, source, settings=settings)
    raise KnowledgeIngestionError(f"unsupported source kind: {source.kind}")


async def _fetch_connector_documents(
    session: AsyncSession, source: KnowledgeImportSource
) -> list[SourceDocument]:
    if source.integration_id is None:
        raise KnowledgeIngestionError(f"{source.kind} source is missing integration_id")
    integration = await session.get(Integration, source.integration_id)
    if integration is None or integration.workspace_id != source.workspace_id:
        raise KnowledgeIngestionError("integration is not available for this workspace")
    refs = source.config.get("resource_refs") or source.config.get("resources")
    if refs is None:
        refs = [source.config.get("resource_ref") or {}]
    if isinstance(refs, dict):
        refs = [refs]
    if not isinstance(refs, list):
        raise KnowledgeIngestionError("connector source config.resource_refs must be a list")
    # Empty list silently produced a "synced 0 of nothing" success — every
    # field was 0 in the result panel and operators (incl. closed-beta
    # clients) thought the connection was broken. Reject early so both
    # the wizard and direct API callers get a clear error instead.
    if not any(isinstance(ref, dict) and ref for ref in refs):
        raise KnowledgeIngestionError(
            "connector source needs at least one resource_ref "
            "(e.g. {\"page_id\": \"...\"} or {\"database_id\": \"...\"})"
        )
    documents: list[SourceDocument] = []
    for ref in refs[:MAX_SOURCE_DOCUMENTS]:
        if not isinstance(ref, dict):
            continue
        for page in await fetch_connector_pages(integration, ref):
            documents.append(
                SourceDocument(
                    external_id=str(page.page_ref.get("page_id") or page.slug),
                    title=page.title,
                    body_md=page.body_md,
                    external_url=_extract_url(page.page_ref),
                    item_ref={"provider": source.kind, **dict(page.page_ref)},
                    cursor=dict(page.page_ref),
                )
            )
    return documents


async def _fetch_firecrawl_documents(
    source: KnowledgeImportSource, *, settings: Settings
) -> list[SourceDocument]:
    if not settings.firecrawl_api_key:
        raise KnowledgeIngestionError("FIRECRAWL_API_KEY is not configured")
    config = source.config or {}
    urls = config.get("urls")
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list) or not urls:
        root_url = str(config.get("url") or config.get("root_url") or "").strip()
        if not root_url:
            raise KnowledgeIngestionError("website source needs config.url or config.urls")
        urls = await _firecrawl_map(root_url, config=config, settings=settings)
    documents: list[SourceDocument] = []
    for raw_url in [str(url).strip() for url in urls if str(url).strip()][
        : int(config.get("limit") or 25)
    ]:
        payload = await _firecrawl_scrape(raw_url, config=config, settings=settings)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        markdown = str(data.get("markdown") or "").strip()
        if not markdown:
            continue
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        title = str(metadata.get("title") or _title_from_url(raw_url))
        change = data.get("changeTracking")
        documents.append(
            SourceDocument(
                external_id=raw_url,
                title=title,
                body_md=markdown,
                external_url=raw_url,
                item_ref={"provider": "firecrawl", "metadata": metadata, "change": change},
                cursor={"url": raw_url, "change": change},
            )
        )
    return documents


async def _firecrawl_map(
    root_url: str, *, config: dict[str, Any], settings: Settings
) -> list[str]:
    payload = await _firecrawl_post(
        "/map",
        {
            "url": root_url,
            "limit": int(config.get("limit") or 25),
            "sitemap": config.get("sitemap") or "include",
            "includeSubdomains": bool(config.get("include_subdomains", False)),
        },
        settings=settings,
    )
    candidates = payload.get("links") or payload.get("data") or payload.get("urls") or []
    if isinstance(candidates, dict):
        candidates = candidates.get("links") or candidates.get("urls") or []
    return [str(url) for url in candidates if isinstance(url, str)]


async def _firecrawl_scrape(
    url: str, *, config: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    formats: list[Any] = ["markdown"]
    if config.get("change_tracking", True):
        formats.append(
            {
                "type": "changeTracking",
                "modes": ["git-diff"],
                "tag": config.get("change_tracking_tag") or "ship-knowledge",
            }
        )
    return await _firecrawl_post(
        "/scrape",
        {
            "url": url,
            "formats": formats,
            "onlyMainContent": config.get("only_main_content", True),
        },
        settings=settings,
    )


async def _firecrawl_post(
    path: str, body: dict[str, Any], *, settings: Settings
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            f"{settings.firecrawl_api_url.rstrip('/')}{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is False:
        raise KnowledgeIngestionError(f"Firecrawl failed: {payload}")
    return payload


def _fetch_static_documents(source: KnowledgeImportSource) -> list[SourceDocument]:
    docs = source.config.get("documents") or source.config.get("uploads") or []
    if isinstance(docs, dict):
        docs = [docs]
    if not isinstance(docs, list):
        raise KnowledgeIngestionError("static upload source needs config.documents")
    out: list[SourceDocument] = []
    for index, doc in enumerate(docs[:MAX_SOURCE_DOCUMENTS], start=1):
        if not isinstance(doc, dict):
            continue
        body = str(doc.get("body_md") or doc.get("content") or "").strip()
        if not body:
            continue
        title = str(doc.get("title") or doc.get("filename") or f"Upload {index}")
        external_id = str(doc.get("external_id") or doc.get("filename") or index)
        out.append(
            SourceDocument(
                external_id=external_id,
                title=title,
                body_md=body,
                external_url=doc.get("url"),
                item_ref={
                    "filename": doc.get("filename"),
                    "content_type": doc.get("content_type"),
                },
                cursor={"static": True},
            )
        )
    return out


async def _fetch_docs_repo_documents(
    session: AsyncSession, source: KnowledgeImportSource, *, settings: Settings
) -> list[SourceDocument]:
    if source.repo_id is None:
        raise KnowledgeIngestionError("docs repo source is missing repo_id")
    repo = await session.get(WorkspaceRepo, source.repo_id)
    if repo is None or repo.workspace_id != source.workspace_id:
        raise KnowledgeIngestionError("repo is not available for this workspace")
    if repo.installation_id is None:
        raise KnowledgeIngestionError("repo has no GitHub installation")
    installation = await session.get(GitHubInstallation, repo.installation_id)
    if installation is None:
        raise KnowledgeIngestionError("GitHub installation is not available")
    owner, name = repo.full_name.split("/", 1)
    ref = RepoRef(kind="github", owner=owner, repo=name)
    gateway = GitHubCodeHost(installation.installation_id, settings=settings)
    all_paths = await gateway.list_files(ref, ref_sha=repo.default_branch)
    prefixes = source.config.get("paths") or source.config.get("path_prefixes") or ["docs"]
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    suffixes = tuple(source.config.get("extensions") or [".md", ".mdx", ".txt"])
    # ``config.limit`` falls back to ``MAX_SOURCE_DOCUMENTS`` (5000)
    # so the ingest pulls every matching doc by default. Operators
    # who want to chunk a giant repo can still pin a smaller value
    # via ``config.limit``; the hardcoded 50 default we used to ship
    # silently truncated even modest doc trees (Ship's own
    # documentation/ tree is ~150 files) and was the reason the
    # canonical workspace was capping at "first 50 alphabetical".
    paths = [
        path
        for path in all_paths
        if any(path.startswith(str(prefix).strip("/")) for prefix in prefixes)
        and path.lower().endswith(suffixes)
    ][: int(source.config.get("limit") or MAX_SOURCE_DOCUMENTS)]
    documents: list[SourceDocument] = []
    for path in paths:
        blob = await gateway.get_blob(ref, path=path, ref_sha=repo.default_branch)
        if blob.encoding != "utf-8" or not blob.content.strip():
            continue
        documents.append(
            SourceDocument(
                external_id=path,
                title=path.rsplit("/", 1)[-1],
                body_md=blob.content,
                external_url=f"{repo.html_url}/blob/{repo.default_branch}/{path}",
                item_ref={"path": path, "sha": blob.sha, "repo_id": str(repo.id)},
                cursor={"path": path, "sha": blob.sha, "ref": blob.ref},
            )
        )
    return documents


SOURCE_KIND_IMPORT = "import_source"
NOTE_BODY_CAP = 12_000
NOTE_EXCERPT_CAP = 600


async def _ingest_documents(
    session: AsyncSession,
    *,
    source: KnowledgeImportSource,
    run: KnowledgeIngestionRun,
    documents: list[SourceDocument],
    actor_user_id: uuid.UUID | None,
    settings: Settings,
) -> IngestionStats:
    """Turn each changed source item into ``Improvement(kind='knowledge_note')``.

    The harvester → router → synthesiser pipeline (KB-1..KB-4) picks up
    these rows on its next tick and decides whether they become draft
    articles in any bucket. We do *not* call the distiller here — the
    operator is the one who curates what becomes canonical knowledge,
    and routing is the router's job.
    """
    mutable = IngestionStats(discovered=len(documents)).to_dict()
    now_iso = datetime.now(timezone.utc).isoformat()
    for doc in documents:
        fingerprint = _sha256_text(_normalise_markdown(doc.body_md))
        item = await _upsert_source_item(session, source=source, doc=doc)
        if item.content_fingerprint == fingerprint:
            mutable["skipped"] += 1
            continue
        item.content_fingerprint = fingerprint
        mutable["changed"] += 1
        sections = _split_sections(doc)
        mutable["sections"] += len(sections)
        for section_idx, section in enumerate(sections):
            note = Improvement(
                workspace_id=source.workspace_id,
                repo_id=source.repo_id,
                pipeline_run_id=None,
                kind="knowledge_note",
                title=(section.title or doc.title)[:512],
                body=section.body_md[:NOTE_BODY_CAP],
                impact=None,
                effort=None,
                context={
                    "source_kind": SOURCE_KIND_IMPORT,
                    "source_id": str(item.id),
                    "source_excerpt": section.body_md[:NOTE_EXCERPT_CAP],
                    "import_source_id": str(source.id),
                    "import_source_kind": source.kind,
                    "ingestion_run_id": str(run.id),
                    "external_id": section.external_id,
                    "external_url": doc.external_url,
                    "section_idx": section_idx,
                    "routed_bucket_id": None,
                    "route_confidence": None,
                    "bucket_hint": source.config.get("target_bucket_slug"),
                    "extractor": "import_source_v1",
                    "harvested_at": now_iso,
                },
            )
            session.add(note)
            mutable["notes_created"] += 1
    return IngestionStats(**mutable)


async def _upsert_source_item(
    session: AsyncSession,
    *,
    source: KnowledgeImportSource,
    doc: SourceDocument,
) -> KnowledgeSourceItem:
    """Upsert a ``KnowledgeSourceItem`` row keyed on (source, external_id).

    Persists the full ``body_md`` plus ``body_md_sha`` so the claim
    extractor (next phase) can find un-extracted items via a sha
    mismatch and skip ones that haven't changed since the last
    extract pass. Body diffs vs the stored sha trigger
    ``extracted_at = NULL`` to mark the row pending again — that's
    how a re-pulled Notion page automatically queues a re-extract.
    """
    now = datetime.now(timezone.utc)
    body_md = _normalise_markdown(doc.body_md)
    body_sha = _sha256_text(body_md) if body_md else None
    existing = (
        await session.execute(
            select(KnowledgeSourceItem).where(
                KnowledgeSourceItem.source_id == source.id,
                KnowledgeSourceItem.external_id == doc.external_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.title = doc.title[:512]
        existing.external_url = doc.external_url
        existing.item_ref = doc.item_ref or {}
        existing.cursor = doc.cursor
        existing.last_seen_at = now
        existing.deleted_at = None
        if body_md and existing.body_md_sha != body_sha:
            existing.body_md = body_md
            # Note: do NOT set body_md_sha here — the extractor stamps
            # it after a successful run. Leaving the old sha alongside
            # the new body marks the row pending without losing the
            # "previously-extracted" timestamp until extract finishes.
            existing.extracted_at = None
        return existing
    item = KnowledgeSourceItem(
        id=uuid.uuid4(),
        workspace_id=source.workspace_id,
        source_id=source.id,
        external_id=doc.external_id,
        title=doc.title[:512],
        external_url=doc.external_url,
        item_ref=doc.item_ref or {},
        content_fingerprint=None,
        cursor=doc.cursor,
        last_seen_at=now,
        body_md=body_md or None,
    )
    session.add(item)
    await session.flush()
    return item


def _extract_url(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("url", "web_url", "external_url"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or parsed.netloc
    return path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()


__all__ = [
    "DueSyncResult",
    "IngestionStats",
    "KnowledgeIngestionError",
    "SourceDocument",
    "create_import_source",
    "fetch_source_documents",
    "sync_import_source",
    "sync_due_import_sources",
]
