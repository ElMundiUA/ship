"""Distiller inbound source adapters.

Two responsibilities live here now:

- **Bucket resolution** (``ensure_bucket`` / ``ensure_user_memory_bucket``):
  fetch-or-mint a :class:`KnowledgeBucket` for a given scope so the
  user-memory + upload paths can address one without a slug collision.
- **External-static upload adapter** (``ingest_external_static_upload``):
  turn a multipart upload into a bucket article via the distiller.
  Connector-proxy and PR-merged ingest were retired alongside Pipeline C
  / user-driven bucket creation; import-source ingest now flows through
  the harvester (``Improvement(kind='knowledge_note')``) instead.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bucket resolution
# ---------------------------------------------------------------------------


async def ensure_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str,
    scope_kind: str = BucketScope.WORKSPACE,
    source_kind: str = BucketSource.EXTERNAL_STATIC,
    repo_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    description: str | None = None,
) -> KnowledgeBucket:
    """Fetch or create a bucket matching the given scope carrier.

    Lookup is keyed on ``(workspace_id, scope_kind, carrier_id,
    slug)`` — the same tuple Phase 3's resolver treats as unique.
    If the bucket exists we reuse it; otherwise we mint it with
    the supplied ``name``/``description`` defaults.

    Validation mirrors the ``ck_knowledge_buckets_scope_carrier``
    CHECK constraint: repo scope needs ``repo_id``, project scope
    needs ``project_id``, user scope needs ``user_id``, workspace
    scope takes none. We raise early with a readable error instead
    of relying on the DB error path.
    """
    if scope_kind == BucketScope.WORKSPACE:
        if repo_id or project_id or user_id:
            raise ValueError("workspace-scoped bucket cannot have a carrier id")
    elif scope_kind == BucketScope.REPO:
        if not repo_id:
            raise ValueError("repo-scoped bucket requires repo_id")
    elif scope_kind == BucketScope.PROJECT:
        if not project_id:
            raise ValueError("project-scoped bucket requires project_id")
    elif scope_kind == BucketScope.USER:
        if not user_id:
            raise ValueError("user-scoped bucket requires user_id")
    else:
        raise ValueError(f"unknown scope_kind: {scope_kind!r}")

    from sqlalchemy import select

    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace_id,
        KnowledgeBucket.scope_kind == scope_kind,
        KnowledgeBucket.slug == slug,
    )
    if scope_kind == BucketScope.REPO:
        stmt = stmt.where(KnowledgeBucket.repo_id == repo_id)
    elif scope_kind == BucketScope.PROJECT:
        stmt = stmt.where(KnowledgeBucket.project_id == project_id)
    elif scope_kind == BucketScope.USER:
        stmt = stmt.where(KnowledgeBucket.user_id == user_id)

    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing

    # Wrap the INSERT in a SAVEPOINT so a CHECK/integrity failure
    # here doesn't poison the surrounding transaction. Callers (PR
    # webhook, save-to-memory, upload handler) are composed of many
    # independent writes inside one request-scoped session; before
    # this block was nested, a flush crash left the Session in an
    # aborted state and every subsequent ``await session.flush()``
    # from that request raised ``PendingRollbackError`` -- which
    # presented downstream (Navigator, dashboard) as "server-side
    # exception" even though the Distiller was the one at fault.
    # Nested transactions roll back just the SAVEPOINT, so the outer
    # work the caller already did (notifications, audit rows) stays
    # intact and their ``try/except`` can decide what to do.
    savepoint = await session.begin_nested()
    try:
        row = KnowledgeBucket(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            slug=slug,
            name=name,
            description=description,
            scope_kind=scope_kind,
            source_kind=source_kind,
            repo_id=repo_id,
            project_id=project_id,
            user_id=user_id,
        )
        session.add(row)
        await session.flush()
    except IntegrityError as exc:
        await savepoint.rollback()
        logger.error(
            "distiller_sources.ensure_bucket flush failed: "
            "workspace_id=%s slug=%r scope=%s source=%s "
            "repo_id=%s project_id=%s user_id=%s exc=%s",
            workspace_id,
            slug,
            scope_kind,
            source_kind,
            repo_id,
            project_id,
            user_id,
            exc,
        )
        raise
    else:
        await savepoint.commit()
    logger.info(
        "distiller_sources: ensured bucket slug=%s scope=%s src=%s",
        slug,
        scope_kind,
        source_kind,
    )
    return row


# ---------------------------------------------------------------------------
# Per-user memory bucket (Phase 8)
# ---------------------------------------------------------------------------


# Stable slug so the Navigator — and any future retrieval surface —
# can address "my memory" by a predictable key instead of having to
# look up the id first. The surface is ``source_kind=agent_memory``
# so retrieval clauses that gate on that (TopicService,
# search_buckets tool) pick it up with no extra wiring.
USER_MEMORY_SLUG = "my-memory"
USER_MEMORY_NAME = "My memory"
USER_MEMORY_DESCRIPTION = (
    "Private notes saved from chat threads. Only you can read from or "
    "write to this bucket."
)


async def ensure_user_memory_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> KnowledgeBucket:
    """Return (creating if needed) the caller's ``my-memory`` bucket.

    Thin wrapper around :func:`ensure_bucket` that pins every
    per-user bucket to a fixed slug and friendly name. This is what
    the Navigator "save to memory" hook calls before packing a
    thread summary — idempotent, so multiple concurrent first
    writes never race to create two rows thanks to the partial
    ``uq_knowledge_buckets_user_slug`` index.

    Why a shared slug: the resolver's scope ladder makes
    ``my-memory`` at ``scope=user`` shadow any workspace-level
    slug collision automatically, so a workspace-wide bucket with
    the same name would never pollute a user's private memory.
    """
    return await ensure_bucket(
        session,
        workspace_id=workspace_id,
        slug=USER_MEMORY_SLUG,
        name=USER_MEMORY_NAME,
        scope_kind=BucketScope.USER,
        source_kind=BucketSource.AGENT_MEMORY,
        user_id=user_id,
        description=USER_MEMORY_DESCRIPTION,
    )


__all__ = [
    "ensure_bucket",
    "ensure_user_memory_bucket",
]
