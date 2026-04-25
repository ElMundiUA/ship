"""Unified sync entry points for knowledge sources."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketSource,
    KnowledgeBucket,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
)
from backend.app.db.models.tenancy import Integration
from backend.app.services.connectors import (
    ConnectorConfigError,
    ConnectorError,
    ConnectorPage,
    fetch_connector_pages,
    get_fetcher,
)
from backend.app.services.distiller import Classifier, DistillerOutcome
from backend.app.services.distiller_sources import (
    ingest_connector_page,
    ingest_external_static_upload,
)
from backend.app.services.knowledge_sources import (
    ensure_source_for_bucket,
    fingerprint_payload,
    mark_source_error,
    mark_source_synced,
)


class KnowledgeSyncError(RuntimeError):
    """Base error for source sync failures."""


class KnowledgeSyncUnsupported(KnowledgeSyncError):
    """The source is valid, but this adapter/resource shape is unsupported."""


async def ingest_static_upload_source(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    actor_user_id: uuid.UUID | None,
    filename: str,
    content_type: str | None,
    body_md: str,
    classifier: Classifier | None = None,
) -> DistillerOutcome:
    """Record upload source metadata, then distill the uploaded text."""

    content_sha = hashlib.sha256(body_md.encode("utf-8")).hexdigest()
    source = await ensure_source_for_bucket(
        session,
        bucket,
        kind=KnowledgeSourceKind.STATIC_UPLOAD,
        config={
            "uploads": [
                {
                    "filename": filename,
                    "content_type": content_type or "text/markdown",
                    "content_sha": content_sha,
                }
            ]
        },
        status=KnowledgeSourceStatus.SYNCING,
        content_fingerprint=content_sha,
    )
    try:
        outcome = await ingest_external_static_upload(
            session,
            workspace_id=workspace_id,
            bucket=bucket,
            actor_user_id=actor_user_id,
            filename=filename,
            content_type=content_type,
            body_md=body_md,
            classifier=classifier,
        )
    except Exception as exc:
        mark_source_error(source, f"{type(exc).__name__}: {exc}")
        raise
    mark_source_synced(
        source,
        content_fingerprint=content_sha,
        cursor={"filename": filename, "content_sha": content_sha},
    )
    return outcome


async def sync_connector_source(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    actor_user_id: uuid.UUID | None,
    classifier: Classifier | None = None,
) -> DistillerOutcome:
    """Fetch a connector source and distill the first returned page."""

    if bucket.source_kind != BucketSource.CONNECTOR_PROXY:
        raise KnowledgeSyncUnsupported(
            f"sync is only defined for connector_proxy buckets, got {bucket.source_kind!r}"
        )

    source_ref = bucket.source_ref or {}
    integration_kind = str(source_ref.get("integration_kind") or "").strip()
    if not integration_kind:
        raise KnowledgeSyncUnsupported(
            "connector source is missing integration_kind"
        )
    resource_ref = source_ref.get("resource_ref") or {}
    if not isinstance(resource_ref, dict):
        resource_ref = {}

    source = await ensure_source_for_bucket(
        session,
        bucket,
        kind=KnowledgeSourceKind.CONNECTOR,
        config=source_ref,
        status=KnowledgeSourceStatus.SYNCING,
    )

    try:
        if get_fetcher(integration_kind) is None:
            raise KnowledgeSyncUnsupported(
                f"no connector fetcher registered for {integration_kind!r}"
            )
        integration_row = await _load_integration(
            session,
            workspace_id=workspace_id,
            source_ref=source_ref,
            kind=integration_kind,
        )
        pages = await fetch_connector_pages(integration_row, resource_ref)
        if not pages:
            raise KnowledgeSyncUnsupported(
                f"connector {integration_kind!r} returned no pages for resource_ref"
            )
        page = pages[0]
        page_ref = {
            "slug": page.slug,
            "title": page.title,
            **dict(page.page_ref),
            "resource_ref": resource_ref,
        }
        outcome = await ingest_connector_page(
            session,
            workspace_id=workspace_id,
            bucket=bucket,
            actor_user_id=actor_user_id,
            connector_kind=integration_kind,
            page_ref=page_ref,
            body_md=page.body_md,
            classifier=classifier,
        )
    except KnowledgeSyncError as exc:
        mark_source_error(source, str(exc))
        raise
    except (ConnectorConfigError, ConnectorError):
        mark_source_error(source, "connector fetch failed")
        raise
    except Exception as exc:
        mark_source_error(source, f"{type(exc).__name__}: {exc}")
        raise

    mark_source_synced(
        source,
        content_fingerprint=fingerprint_payload(
            {"kind": integration_kind, "resource_ref": resource_ref, "page": page_ref}
        ),
        cursor={"page_ref": page_ref},
    )
    return outcome


async def _load_integration(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_ref: dict[str, Any],
    kind: str,
) -> Integration:
    raw_id = source_ref.get("integration_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise KnowledgeSyncUnsupported("connector source is missing integration_id")
    try:
        integration_id = uuid.UUID(raw_id.strip())
    except ValueError as exc:
        raise KnowledgeSyncUnsupported("connector integration_id is invalid") from exc

    row = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.id == integration_id,
                Integration.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise KnowledgeSyncUnsupported(
            f"integration {integration_id} ({kind}) is not available in this workspace"
        )
    return row


__all__ = [
    "KnowledgeSyncError",
    "KnowledgeSyncUnsupported",
    "ingest_static_upload_source",
    "sync_connector_source",
]
