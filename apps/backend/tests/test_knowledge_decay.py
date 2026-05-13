"""Coverage for ``gc_archived_articles`` — Step 6.

Pins the contract that the daily cron only deletes archived rows past
the TTL cutoff and never touches published / recently-archived rows.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.services.knowledge_decay import (
    ARCHIVE_TTL_DAYS,
    gc_archived_articles,
)


pytestmark = pytest.mark.asyncio


def _content_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _seed_bucket(db_session, workspace_id) -> KnowledgeBucket:
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        slug="product-knowledge",
        name="Product Knowledge",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


def _archived_article(bucket: KnowledgeBucket, *, slug: str, age_days: int) -> BucketArticle:
    archived_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=slug,
        title=slug.title(),
        body_md=f"body for {slug}",
        content_sha=_content_sha(f"body for {slug}"),
        status=BucketArticleStatus.ARCHIVED,
        archived_at=archived_at,
    )


def _published_article(bucket: KnowledgeBucket, *, slug: str) -> BucketArticle:
    return BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=slug,
        title=slug.title(),
        body_md=f"body for {slug}",
        content_sha=_content_sha(f"body for {slug}"),
        status=BucketArticleStatus.PUBLISHED,
    )


async def test_gc_deletes_only_archived_articles_past_ttl(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    bucket = await _seed_bucket(db_session, workspace.id)

    old = _archived_article(bucket, slug="old", age_days=ARCHIVE_TTL_DAYS + 5)
    fresh = _archived_article(bucket, slug="fresh", age_days=ARCHIVE_TTL_DAYS - 5)
    live = _published_article(bucket, slug="live")
    db_session.add_all([old, fresh, live])
    await db_session.flush()

    report = await gc_archived_articles(db_session, workspace_id=workspace.id)
    assert report.deleted == 1
    assert report.inspected == 1

    surviving = list(
        (
            await db_session.execute(
                select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
            )
        )
        .scalars()
        .all()
    )
    surviving_slugs = {row.slug for row in surviving}
    assert surviving_slugs == {"fresh", "live"}


async def test_gc_workspace_scoped(db_session, seed_workspace, seed_user):
    """The sweep doesn't touch articles in other workspaces."""
    from backend.app.db.models.tenancy import Workspace

    _, _, workspace = seed_workspace
    _, org = seed_user

    other = Workspace(
        org_id=org.id, slug=f"ws-{uuid.uuid4().hex[:6]}", name="Other workspace"
    )
    db_session.add(other)
    await db_session.flush()

    bucket = await _seed_bucket(db_session, workspace.id)
    other_bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=other.id,
        slug="product-knowledge",
        name="Product Knowledge",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(other_bucket)
    await db_session.flush()

    target = _archived_article(bucket, slug="old-target", age_days=ARCHIVE_TTL_DAYS + 5)
    other_old = _archived_article(other_bucket, slug="old-other", age_days=ARCHIVE_TTL_DAYS + 5)
    db_session.add_all([target, other_old])
    await db_session.flush()

    report = await gc_archived_articles(db_session, workspace_id=workspace.id)
    assert report.deleted == 1

    other_remaining = list(
        (
            await db_session.execute(
                select(BucketArticle).where(BucketArticle.bucket_id == other_bucket.id)
            )
        )
        .scalars()
        .all()
    )
    assert {row.slug for row in other_remaining} == {"old-other"}


async def test_gc_explicit_cutoff_overrides_default(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    bucket = await _seed_bucket(db_session, workspace.id)

    very_recent = _archived_article(bucket, slug="recent", age_days=2)
    db_session.add(very_recent)
    await db_session.flush()

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    report = await gc_archived_articles(
        db_session, workspace_id=workspace.id, cutoff=cutoff
    )
    assert report.deleted == 1
