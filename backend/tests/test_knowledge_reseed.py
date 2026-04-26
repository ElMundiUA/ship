from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    BucketSummary,
    DistillerRun,
    KnowledgeBucket,
    KnowledgeSource,
)
from backend.app.db.models.knowledge_promotion import KnowledgePromotionCandidate
from backend.app.services.knowledge_reseed import (
    RECOMMENDED_BUCKETS,
    build_backup_snapshot,
    preview_reseed_counts,
    reseed_workspace_knowledge,
)


pytestmark = pytest.mark.asyncio


async def test_reseed_deletes_old_knowledge_and_creates_recommended_buckets(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="old",
        name="Old",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()

    article = BucketArticle(
        bucket_id=bucket.id,
        slug="main",
        title="Old article",
        body_md="old",
        content_sha=hashlib.sha256(b"old").hexdigest(),
    )
    db_session.add_all(
        [
            KnowledgeSource(
                workspace_id=workspace.id,
                bucket_id=bucket.id,
                kind="agent_memory",
                config={},
            ),
            BucketSummary(bucket_id=bucket.id, title="Old summary", summary="old"),
            article,
            DistillerRun(
                workspace_id=workspace.id,
                bucket_id=bucket.id,
                source_kind="agent_memory",
            ),
            KnowledgePromotionCandidate(
                workspace_id=workspace.id,
                fingerprint="fp",
                article_ids=[],
                slug_hint="old",
                ttl_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ]
    )
    await db_session.flush()

    counts = await preview_reseed_counts(db_session, [workspace.id])
    assert counts.workspaces == 1
    assert counts.buckets_deleted == 1
    assert counts.articles_deleted == 1
    assert counts.sources_deleted == 1
    assert counts.summaries_deleted == 1
    assert counts.distiller_runs_deleted == 1
    assert counts.candidates_deleted == 1
    assert counts.buckets_created == len(RECOMMENDED_BUCKETS)

    backup = await build_backup_snapshot(db_session, [workspace.id])
    assert backup["tables"]["knowledge_buckets"][0]["slug"] == "old"
    assert backup["tables"]["bucket_articles"][0]["title"] == "Old article"

    final_counts = await reseed_workspace_knowledge(db_session, [workspace.id])
    assert final_counts == counts

    rows = list(
        (
            await db_session.execute(
                select(KnowledgeBucket).where(
                    KnowledgeBucket.workspace_id == workspace.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [row.slug for row in rows] == [spec.slug for spec in RECOMMENDED_BUCKETS]
    assert {row.scope_kind for row in rows} == {BucketScope.WORKSPACE}
    assert all(row.repo_id is None for row in rows)

    article_count = (
        await db_session.execute(select(func.count()).select_from(BucketArticle))
    ).scalar_one()
    assert article_count == 0

