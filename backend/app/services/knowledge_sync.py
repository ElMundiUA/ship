"""Unified sync entry points for knowledge sources."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    KnowledgeBucket,
    KnowledgeSourceKind,
    KnowledgeSourceStatus,
)
from backend.app.services.distiller import Classifier, DistillerOutcome
from backend.app.services.distiller_sources import ingest_external_static_upload
from backend.app.services.knowledge_sources import (
    ensure_source_for_bucket,
    mark_source_error,
    mark_source_synced,
)


class KnowledgeSyncError(RuntimeError):
    """Base error for source sync failures."""


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


__all__ = [
    "KnowledgeSyncError",
    "ingest_static_upload_source",
]
