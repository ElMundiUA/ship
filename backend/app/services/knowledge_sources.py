"""Helpers for the durable knowledge-source layer."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketSource,
    KnowledgeBucket,
    KnowledgeSource,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
)


def source_kind_for_bucket(bucket_source_kind: str) -> str:
    """Map legacy bucket ``source_kind`` values to source adapter kinds."""

    if bucket_source_kind == BucketSource.CONNECTOR_PROXY:
        return KnowledgeSourceKind.CONNECTOR
    if bucket_source_kind == BucketSource.EXTERNAL_STATIC:
        return KnowledgeSourceKind.STATIC_UPLOAD
    if bucket_source_kind == BucketSource.REPO_CONTEXT:
        return KnowledgeSourceKind.REPO_CONTEXT
    return bucket_source_kind


def fingerprint_payload(value: Any) -> str:
    """Stable short SHA for source cursors/config/body metadata."""

    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


async def ensure_source_for_bucket(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    *,
    kind: str | None = None,
    config: dict[str, Any] | None = None,
    status: str = KnowledgeSourceStatus.READY,
    content_fingerprint: str | None = None,
) -> KnowledgeSource:
    """Fetch or create the primary source row for ``bucket``.

    The lookup intentionally keys by ``bucket_id`` + ``kind``. Buckets
    can grow additional source rows later, but the compatibility layer
    needs one stable primary source per legacy bucket.
    """

    resolved_kind = kind or source_kind_for_bucket(bucket.source_kind)
    existing = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.bucket_id == bucket.id,
                KnowledgeSource.kind == resolved_kind,
            )
        )
    ).scalars().first()

    if existing is not None:
        if config is not None:
            existing.config = config
        if content_fingerprint is not None:
            existing.content_fingerprint = content_fingerprint
        existing.status = status
        if status != KnowledgeSourceStatus.ERROR:
            existing.last_error = None
        return existing

    source = KnowledgeSource(
        id=uuid.uuid4(),
        workspace_id=bucket.workspace_id,
        bucket_id=bucket.id,
        kind=resolved_kind,
        config=config if config is not None else (bucket.source_ref or {}),
        status=status,
        content_fingerprint=content_fingerprint,
    )
    session.add(source)
    await session.flush()
    return source


def mark_source_synced(
    source: KnowledgeSource,
    *,
    content_fingerprint: str | None = None,
    cursor: dict[str, Any] | None = None,
) -> None:
    source.status = KnowledgeSourceStatus.READY
    source.last_synced_at = datetime.now(timezone.utc)
    source.last_error = None
    if content_fingerprint is not None:
        source.content_fingerprint = content_fingerprint
    if cursor is not None:
        source.cursor = cursor


def mark_source_error(source: KnowledgeSource, error: str) -> None:
    source.status = KnowledgeSourceStatus.ERROR
    source.last_error = error[:4000]


__all__ = [
    "ensure_source_for_bucket",
    "fingerprint_payload",
    "mark_source_error",
    "mark_source_synced",
    "source_kind_for_bucket",
]
