"""Destructive workspace knowledge reseed.

The post-PR-knowledge model stores workspace knowledge only in Ship's DB.
This module owns the canonical starter bucket definitions and the one-shot
cleanup/reseed routine used by the operator script.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    BucketSummary,
    DistillerRun,
    KbChunk,
    KnowledgeBucket,
    KnowledgeSource,
)
from backend.app.db.models.knowledge_promotion import KnowledgePromotionCandidate
from backend.app.db.models.tenancy import Workspace


@dataclass(frozen=True)
class RecommendedBucket:
    slug: str
    name: str
    description: str
    bucket_type: str
    purpose: str
    authority: str
    access_level: str = "Workspace"
    freshness_policy: str = "Manual refresh"


RECOMMENDED_BUCKETS: tuple[RecommendedBucket, ...] = (
    RecommendedBucket(
        slug="project-map",
        name="Project Map",
        description="Repository structure, service ownership, and where important files live.",
        bucket_type="Project Map",
        purpose="Use for where/which repo/which folder/ownership/project structure questions.",
        authority="High-confidence reference",
    ),
    RecommendedBucket(
        slug="architecture-decisions",
        name="Architecture Decisions",
        description="ADRs, design docs, trade-offs, and why the system is designed this way.",
        bucket_type="Architecture",
        purpose="Use for architectural rationale, major design choices, and trade-offs.",
        authority="Source of truth",
    ),
    RecommendedBucket(
        slug="engineering-standards",
        name="Engineering Standards",
        description="Coding standards, review expectations, testing conventions, and delivery rules.",
        bucket_type="Engineering Standards",
        purpose="Use for implementation style, code review, test coverage, and team conventions.",
        authority="Source of truth",
    ),
    RecommendedBucket(
        slug="runbooks-operations",
        name="Runbooks & Operations",
        description="Deployment, rollback, incident response, recovery, and on-call procedures.",
        bucket_type="Runbooks",
        purpose="Use for deploy, rollback, incident, recovery, rotation, and operational checklists.",
        authority="Source of truth",
    ),
    RecommendedBucket(
        slug="product-knowledge",
        name="Product Knowledge",
        description="Product behavior, customer-facing concepts, roadmap context, and UX decisions.",
        bucket_type="Product Knowledge",
        purpose="Use for product semantics, customer promises, and user-facing workflows.",
        authority="High-confidence reference",
    ),
    RecommendedBucket(
        slug="source-intelligence",
        name="Source Intelligence",
        description="Ship-generated codebase understanding, code maps, and repository analysis.",
        bucket_type="Source Intelligence",
        purpose="Use for generated code intelligence that Ship owns in the database.",
        authority="Generated / low-authority",
    ),
    RecommendedBucket(
        slug="generated-assets",
        name="Generated Assets",
        description="Agent-generated artifacts, summaries, and useful derived knowledge.",
        bucket_type="Generated Assets",
        purpose="Use for generated outputs that should remain discoverable but low authority.",
        authority="Generated / low-authority",
    ),
    RecommendedBucket(
        slug="security-access",
        name="Security & Access",
        description="Security practices, access model, secrets handling, and production permissions.",
        bucket_type="Security",
        purpose="Use for security controls, access decisions, secrets, and restricted operational knowledge.",
        authority="Source of truth",
        access_level="Restricted",
    ),
    RecommendedBucket(
        slug="integration-playbooks",
        name="Integration Playbooks",
        description="How external systems are connected and operated: trackers, docs, CI, cloud, and APIs.",
        bucket_type="Integrations",
        purpose="Use for setup and troubleshooting of third-party integrations.",
        authority="High-confidence reference",
    ),
    RecommendedBucket(
        slug="data-domain-glossary",
        name="Data & Domain Glossary",
        description="Domain terms, core entities, data definitions, metrics, and business vocabulary.",
        bucket_type="Data Glossary",
        purpose="Use for definitions, entity meaning, metrics, and shared vocabulary.",
        authority="Source of truth",
    ),
)


@dataclass(frozen=True)
class ReseedCounts:
    workspaces: int
    buckets_deleted: int
    articles_deleted: int
    summaries_deleted: int
    sources_deleted: int
    chunks_deleted: int
    distiller_runs_deleted: int
    candidates_deleted: int
    buckets_created: int


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _jsonable(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _metadata_for(spec: RecommendedBucket) -> dict[str, Any]:
    return {
        "knowledge_metadata": {
            "purpose": spec.purpose,
            "bucket_type": spec.bucket_type,
            "authority": spec.authority,
            "access_level": spec.access_level,
            "freshness_policy": spec.freshness_policy,
            "starter_bucket": True,
            "reseeded_at": datetime.now(timezone.utc).isoformat(),
        }
    }


async def list_workspace_ids(
    session: AsyncSession, workspace_ids: list[uuid.UUID] | None = None
) -> list[uuid.UUID]:
    stmt = select(Workspace.id).order_by(Workspace.created_at)
    if workspace_ids:
        stmt = stmt.where(Workspace.id.in_(workspace_ids))
    return list((await session.execute(stmt)).scalars().all())


async def build_backup_snapshot(
    session: AsyncSession, workspace_ids: list[uuid.UUID]
) -> dict[str, Any]:
    """Return the rows this reseed will delete.

    The snapshot is JSON-serializable and intentionally table-shaped so an
    operator can inspect or restore manually if a destructive run was pointed
    at the wrong database.
    """

    bucket_ids = list(
        (
            await session.execute(
                select(KnowledgeBucket.id).where(
                    KnowledgeBucket.workspace_id.in_(workspace_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for name, model, workspace_filter in (
        ("knowledge_buckets", KnowledgeBucket, KnowledgeBucket.workspace_id),
        ("knowledge_sources", KnowledgeSource, KnowledgeSource.workspace_id),
        ("bucket_summaries", BucketSummary, None),
        ("bucket_articles", BucketArticle, None),
        ("kb_chunks", KbChunk, KbChunk.workspace_id),
        ("distiller_runs", DistillerRun, DistillerRun.workspace_id),
        (
            "knowledge_promotion_candidates",
            KnowledgePromotionCandidate,
            KnowledgePromotionCandidate.workspace_id,
        ),
    ):
        stmt = select(model)
        if workspace_filter is not None:
            stmt = stmt.where(workspace_filter.in_(workspace_ids))
        elif bucket_ids:
            stmt = stmt.where(model.bucket_id.in_(bucket_ids))
        else:
            tables[name] = []
            continue
        rows = list((await session.execute(stmt)).scalars().all())
        tables[name] = [_row_to_dict(row) for row in rows]

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workspace_ids": [str(workspace_id) for workspace_id in workspace_ids],
        "recommended_buckets": [
            {
                "slug": spec.slug,
                "name": spec.name,
                "description": spec.description,
                "bucket_type": spec.bucket_type,
                "authority": spec.authority,
            }
            for spec in RECOMMENDED_BUCKETS
        ],
        "tables": tables,
    }


async def preview_reseed_counts(
    session: AsyncSession, workspace_ids: list[uuid.UUID]
) -> ReseedCounts:
    bucket_ids = list(
        (
            await session.execute(
                select(KnowledgeBucket.id).where(
                    KnowledgeBucket.workspace_id.in_(workspace_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    async def count(stmt: Any) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    bucket_filter = BucketArticle.bucket_id.in_(bucket_ids) if bucket_ids else False
    summary_filter = BucketSummary.bucket_id.in_(bucket_ids) if bucket_ids else False
    return ReseedCounts(
        workspaces=len(workspace_ids),
        buckets_deleted=await count(
            select(func.count()).select_from(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id.in_(workspace_ids)
            )
        ),
        articles_deleted=(
            await count(
                select(func.count()).select_from(BucketArticle).where(bucket_filter)
            )
            if bucket_ids
            else 0
        ),
        summaries_deleted=(
            await count(
                select(func.count()).select_from(BucketSummary).where(summary_filter)
            )
            if bucket_ids
            else 0
        ),
        sources_deleted=await count(
            select(func.count()).select_from(KnowledgeSource).where(
                KnowledgeSource.workspace_id.in_(workspace_ids)
            )
        ),
        chunks_deleted=await count(
            select(func.count()).select_from(KbChunk).where(
                KbChunk.workspace_id.in_(workspace_ids)
            )
        ),
        distiller_runs_deleted=await count(
            select(func.count()).select_from(DistillerRun).where(
                DistillerRun.workspace_id.in_(workspace_ids)
            )
        ),
        candidates_deleted=await count(
            select(func.count()).select_from(KnowledgePromotionCandidate).where(
                KnowledgePromotionCandidate.workspace_id.in_(workspace_ids)
            )
        ),
        buckets_created=len(workspace_ids) * len(RECOMMENDED_BUCKETS),
    )


async def reseed_workspace_knowledge(
    session: AsyncSession, workspace_ids: list[uuid.UUID]
) -> ReseedCounts:
    """Delete all existing knowledge rows for workspaces and recreate starters."""

    counts = await preview_reseed_counts(session, workspace_ids)
    bucket_ids = list(
        (
            await session.execute(
                select(KnowledgeBucket.id).where(
                    KnowledgeBucket.workspace_id.in_(workspace_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    await session.execute(
        delete(KnowledgePromotionCandidate).where(
            KnowledgePromotionCandidate.workspace_id.in_(workspace_ids)
        )
    )
    await session.execute(
        delete(KbChunk).where(KbChunk.workspace_id.in_(workspace_ids))
    )
    await session.execute(
        delete(DistillerRun).where(DistillerRun.workspace_id.in_(workspace_ids))
    )
    await session.execute(
        delete(KnowledgeSource).where(KnowledgeSource.workspace_id.in_(workspace_ids))
    )
    if bucket_ids:
        await session.execute(
            delete(BucketSummary).where(BucketSummary.bucket_id.in_(bucket_ids))
        )
        await session.execute(
            delete(BucketArticle).where(BucketArticle.bucket_id.in_(bucket_ids))
        )
    await session.execute(
        delete(KnowledgeBucket).where(KnowledgeBucket.workspace_id.in_(workspace_ids))
    )

    for workspace_id in workspace_ids:
        for spec in RECOMMENDED_BUCKETS:
            session.add(
                KnowledgeBucket(
                    workspace_id=workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    description=spec.description,
                    scope_kind=BucketScope.WORKSPACE,
                    source_kind=BucketSource.EXTERNAL_STATIC,
                    source_ref=_metadata_for(spec),
                )
            )
    await session.flush()
    return counts

