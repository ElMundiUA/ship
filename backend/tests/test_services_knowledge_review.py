"""KB-4 (ELS-38) operator-review side-effects.

Synthesiser drops one ``InboxItem(type='improvement',
source_table='bucket_articles', payload.kind='auto_routed_draft')``
per draft. Operator's ``accept`` flips the draft to published and
supersedes the prior; ``dismiss`` archives the draft.

Tests drive ``apply_side_effects`` directly so we don't depend on
the route's auth/transaction plumbing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.inbox import InboxItem
from backend.app.services.inbox.side_effects import apply_side_effects


@pytest_asyncio.fixture
async def workspace_with_draft(db_session, seed_workspace):
    """Workspace + bucket + one DRAFT BucketArticle + matching InboxItem."""
    user, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
        slug="architecture-decisions",
        name="Architecture Decisions",
        description=None,
    )
    db_session.add(bucket)
    await db_session.flush()

    draft = BucketArticle(
        bucket_id=bucket.id,
        slug="branchless-exit-protocol",
        title="ADR: branchless exit protocol",
        body_md="## Decision\n\nAgents post to /agent-runs/finish.",
        content_sha="d" * 64,
        version=1,
        status=BucketArticleStatus.DRAFT,
        embedding=None,
    )
    db_session.add(draft)
    await db_session.flush()

    item = InboxItem(
        workspace_id=workspace.id,
        repo_id=None,
        type="improvement",
        title="Draft article: branchless exit protocol",
        summary="## Decision  Agents post...",
        payload={
            "kind": "auto_routed_draft",
            "article_id": str(draft.id),
            "bucket_id": str(bucket.id),
            "bucket_slug": bucket.slug,
            "article_slug": draft.slug,
            "action": "new",
            "version": 1,
            "source_note_count": 2,
        },
        status="resolved",  # the disposition route flips this before side-effects run
        source_table="bucket_articles",
        source_id=draft.id,
    )
    db_session.add(item)
    await db_session.flush()
    return user, workspace, bucket, draft, item


@pytest.mark.asyncio
async def test_accept_publishes_new_draft(db_session, workspace_with_draft):
    """``accept`` flips draft → published and bumps bucket.updated_at."""
    user, _ws, bucket, draft, item = workspace_with_draft
    bucket_updated_before = bucket.updated_at

    report = await apply_side_effects(
        db_session,
        item=item,
        action="accept",
        payload={},
        actor_user_id=user.id,
    )
    assert draft.id in report.legacy_writebacks
    assert report.failures == []

    refreshed = await db_session.get(BucketArticle, draft.id)
    assert refreshed.status == BucketArticleStatus.PUBLISHED

    refreshed_bucket = await db_session.get(KnowledgeBucket, bucket.id)
    assert refreshed_bucket.updated_at > bucket_updated_before


@pytest.mark.asyncio
async def test_accept_supersedes_prior_published(db_session, workspace_with_draft):
    """When draft.supersedes_id is set, prior published row → superseded."""
    user, _ws, bucket, draft, item = workspace_with_draft

    # Bump the fixture draft to v2 first so its (bucket, slug, version)
    # row no longer collides with the prior published v1 we're about
    # to insert.
    draft.version = 2
    await db_session.flush()

    prev = BucketArticle(
        bucket_id=bucket.id,
        slug=draft.slug,  # same slug — versioned chain
        title="ADR: branchless exit protocol",
        body_md="## Decision (v1)",
        content_sha="a" * 64,
        version=1,
        status=BucketArticleStatus.PUBLISHED,
    )
    db_session.add(prev)
    await db_session.flush()

    draft.supersedes_id = prev.id
    await db_session.flush()

    await apply_side_effects(
        db_session, item=item, action="accept", payload={}, actor_user_id=user.id
    )

    refreshed_prev = await db_session.get(BucketArticle, prev.id)
    refreshed_draft = await db_session.get(BucketArticle, draft.id)
    assert refreshed_prev.status == BucketArticleStatus.SUPERSEDED
    assert refreshed_draft.status == BucketArticleStatus.PUBLISHED


@pytest.mark.asyncio
async def test_accept_idempotent_on_already_published(
    db_session, workspace_with_draft
):
    """Second accept on the same item → idempotent noop."""
    user, _ws, _bucket, draft, item = workspace_with_draft

    # First accept publishes.
    await apply_side_effects(
        db_session, item=item, action="accept", payload={}, actor_user_id=user.id
    )
    refreshed_first = await db_session.get(BucketArticle, draft.id)
    assert refreshed_first.status == BucketArticleStatus.PUBLISHED

    # Second accept: no error, still published.
    report = await apply_side_effects(
        db_session, item=item, action="accept", payload={}, actor_user_id=user.id
    )
    assert report.failures == []
    refreshed_second = await db_session.get(BucketArticle, draft.id)
    assert refreshed_second.status == BucketArticleStatus.PUBLISHED


@pytest.mark.asyncio
async def test_dismiss_archives_draft(db_session, workspace_with_draft):
    """``dismiss`` sets archived_at on the draft; status stays 'draft'."""
    user, _ws, _bucket, draft, item = workspace_with_draft

    await apply_side_effects(
        db_session, item=item, action="dismiss", payload={}, actor_user_id=user.id
    )
    refreshed = await db_session.get(BucketArticle, draft.id)
    assert refreshed.archived_at is not None
    assert refreshed.status == BucketArticleStatus.DRAFT  # status untouched


@pytest.mark.asyncio
async def test_legacy_improvement_accept_unaffected(db_session, seed_workspace):
    """A non-knowledge improvement (source_table='improvements') still flows
    through the legacy writeback, not the new draft-publish path."""
    from backend.app.db.models.agent_surface import Improvement

    user, _, workspace = seed_workspace
    legacy = Improvement(
        workspace_id=workspace.id,
        kind="refactor",
        title="legacy improvement",
        body="...",
    )
    db_session.add(legacy)
    await db_session.flush()

    item = InboxItem(
        workspace_id=workspace.id,
        repo_id=None,
        type="improvement",
        title="legacy",
        summary=None,
        payload={"kind": "legacy"},
        status="resolved",
        source_table="improvements",
        source_id=legacy.id,
    )
    db_session.add(item)
    await db_session.flush()

    report = await apply_side_effects(
        db_session, item=item, action="accept", payload={}, actor_user_id=user.id
    )
    assert legacy.id in report.legacy_writebacks
    refreshed = await db_session.get(Improvement, legacy.id)
    assert refreshed.decision == "accepted"
