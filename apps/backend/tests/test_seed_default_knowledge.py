"""Tests for default knowledge bucket seeding on workspace creation (E01 T07).

Verifies that a freshly-created workspace lands with one default knowledge
bucket (``product-knowledge``) plus one starter article describing what just
happened (GitHub App installed, repos bound, tracker configured).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def seed_workspace_and_repo(db_session: AsyncSession, seed_workspace):
    """Create a workspace + one activated repo for seeding tests."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from datetime import datetime, timezone

    user, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=888_001,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_888_001,
        full_name="acme/demo-repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/demo-repo",
        activated_at=datetime.now(timezone.utc),
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    return user, raw, workspace, install, repo


@pytest.mark.asyncio
async def test_seed_default_knowledge_creates_bucket_and_article(
    db_session: AsyncSession, seed_workspace_and_repo
) -> None:
    """Fresh workspace + first repo activation seeds one bucket + one article."""
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketArticleStatus,
        BucketScope,
        KnowledgeBucket,
    )
    from backend.app.services.seed_bundle import seed_default_knowledge

    user, raw, workspace, install, repo = seed_workspace_and_repo

    result = await seed_default_knowledge(
        session=db_session,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        repo_names=["acme/demo-repo"],
        tracker_provider=None,
    )

    assert result["bucket_created"] is True
    assert result["article_created"] is True

    # Verify bucket exists
    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace.id,
        KnowledgeBucket.slug == "product-knowledge",
    )
    bucket = (await db_session.execute(stmt)).scalars().first()
    assert bucket is not None
    assert bucket.name == "Product knowledge"
    assert bucket.scope_kind == BucketScope.WORKSPACE

    # Verify article exists
    stmt = select(BucketArticle).where(
        BucketArticle.bucket_id == bucket.id,
        BucketArticle.slug == "workspace-setup",
    )
    article = (await db_session.execute(stmt)).scalars().first()
    assert article is not None
    assert article.title == "How this workspace was set up"
    assert article.version == 1
    assert article.status == BucketArticleStatus.PUBLISHED
    assert "acme/demo-repo" in article.body_md
    assert workspace.name in article.body_md
    assert "GitHub App" in article.body_md


@pytest.mark.asyncio
async def test_seed_default_knowledge_idempotent(
    db_session: AsyncSession, seed_workspace_and_repo
) -> None:
    """Re-running seed does not duplicate the bucket or article."""
    from backend.app.db.models.agent_memory import BucketArticle, KnowledgeBucket
    from backend.app.services.seed_bundle import seed_default_knowledge
    from sqlalchemy import func

    user, raw, workspace, install, repo = seed_workspace_and_repo

    # First call
    result1 = await seed_default_knowledge(
        session=db_session,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        repo_names=["acme/demo-repo"],
        tracker_provider=None,
    )
    assert result1["bucket_created"] is True

    # Count buckets
    stmt = select(func.count(KnowledgeBucket.id)).where(
        KnowledgeBucket.workspace_id == workspace.id,
        KnowledgeBucket.slug == "product-knowledge",
    )
    count1 = (await db_session.execute(stmt)).scalar()
    assert count1 == 1

    # Second call (idempotency check)
    result2 = await seed_default_knowledge(
        session=db_session,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        repo_names=["acme/demo-repo"],
        tracker_provider="linear",
    )
    assert result2["bucket_created"] is False
    assert result2["article_created"] is False

    # Count buckets again — should still be 1
    count2 = (await db_session.execute(stmt)).scalar()
    assert count2 == 1


@pytest.mark.asyncio
async def test_seed_default_knowledge_with_tracker(
    db_session: AsyncSession, seed_workspace_and_repo
) -> None:
    """Tracker provider is documented in the starter article."""
    from backend.app.db.models.agent_memory import BucketArticle, KnowledgeBucket
    from backend.app.services.seed_bundle import seed_default_knowledge

    user, raw, workspace, install, repo = seed_workspace_and_repo

    result = await seed_default_knowledge(
        session=db_session,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        repo_names=["acme/demo-repo", "acme/api-server"],
        tracker_provider="linear",
    )

    assert result["bucket_created"] is True

    # Fetch the article and verify tracker is mentioned
    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace.id,
        KnowledgeBucket.slug == "product-knowledge",
    )
    bucket = (await db_session.execute(stmt)).scalars().first()

    stmt = select(BucketArticle).where(
        BucketArticle.bucket_id == bucket.id,
        BucketArticle.slug == "workspace-setup",
    )
    article = (await db_session.execute(stmt)).scalars().first()

    assert "Issue Tracker" in article.body_md
    assert "linear" in article.body_md
    assert "acme/demo-repo" in article.body_md
    assert "acme/api-server" in article.body_md


@pytest.mark.asyncio
async def test_seed_default_knowledge_no_repos(
    db_session: AsyncSession, seed_workspace
) -> None:
    """Seeding with no repos specified still creates bucket and article."""
    from backend.app.db.models.agent_memory import BucketArticle, KnowledgeBucket
    from backend.app.services.seed_bundle import seed_default_knowledge

    user, raw, workspace = seed_workspace

    result = await seed_default_knowledge(
        session=db_session,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        repo_names=None,
        tracker_provider=None,
    )

    assert result["bucket_created"] is True

    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace.id,
        KnowledgeBucket.slug == "product-knowledge",
    )
    bucket = (await db_session.execute(stmt)).scalars().first()
    assert bucket is not None

    stmt = select(BucketArticle).where(
        BucketArticle.bucket_id == bucket.id,
        BucketArticle.slug == "workspace-setup",
    )
    article = (await db_session.execute(stmt)).scalars().first()
    assert article is not None
    assert "No repos selected yet" in article.body_md
