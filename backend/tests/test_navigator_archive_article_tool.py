"""Navigator ``archive_bucket_article`` tool.

The agent-side mirror of the
``POST /v1/workspaces/.../buckets/.../articles/.../archive`` route.
Audit-row shape is intentionally aligned (action prefix
``navigator.tool.archive_bucket_article``) so a forensic sweep can
join HTTP and chat surfaces on the same target id.

Coverage:

- Happy path: published article flips to ``archived`` with
  ``archived_at`` set, audit row carries the reason.
- Idempotency: re-archiving an already-archived article is a no-op
  with ``already_archived=True`` and no second audit row.
- Validation: missing / empty ``reason`` returns
  ``error='validation_failed'`` without touching the row.
- Tenancy: an article id from another workspace returns
  ``error='not_found'``.
- Admin gating: a member-role caller gets ``error='forbidden'``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.tenancy import AuditLog


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def _make_bucket(db_session, *, workspace_id, slug="architecture-decisions"):
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        slug=slug,
        name="Architecture Decisions",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


async def _make_article(
    db_session,
    bucket: KnowledgeBucket,
    *,
    slug: str = "live-decision",
    status: str = BucketArticleStatus.PUBLISHED,
    archived: bool = False,
):
    body = "decision body"
    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=slug,
        title="ADR: live decision",
        body_md=body,
        content_sha=hashlib.sha256(body.encode()).hexdigest(),
        version=1,
        status=status,
        provenance={"source_kind": bucket.source_kind},
        archived_at=(
            datetime.now(timezone.utc) if archived else None
        ),
    )
    db_session.add(article)
    await db_session.flush()
    return article


@pytest.mark.asyncio
async def test_archive_article_happy_path(db_session, seed_workspace) -> None:
    user, _, ws = seed_workspace
    bucket = await _make_bucket(db_session, workspace_id=ws.id)
    article = await _make_article(db_session, bucket)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    raw = await box.invoke(
        "archive_bucket_article",
        {
            "article_id": str(article.id),
            "reason": "Reverted in commit cf9f983 — see ADR ELS-32.",
        },
    )
    out = json.loads(raw)
    assert out["status"] == "archived"
    assert out["already_archived"] is False
    assert out["bucket_slug"] == bucket.slug

    fresh = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.id == article.id)
        )
    ).scalar_one()
    assert fresh.status == BucketArticleStatus.ARCHIVED
    assert fresh.archived_at is not None

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "navigator.tool.archive_bucket_article",
                AuditLog.target_id == str(article.id),
            )
        )
    ).scalars().all()
    assert len(audit) == 1
    assert audit[0].payload["reason"].startswith("Reverted in commit")


@pytest.mark.asyncio
async def test_archive_article_idempotent(db_session, seed_workspace) -> None:
    user, _, ws = seed_workspace
    bucket = await _make_bucket(db_session, workspace_id=ws.id)
    article = await _make_article(
        db_session, bucket, archived=True, status=BucketArticleStatus.ARCHIVED
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "archive_bucket_article",
            {"article_id": str(article.id), "reason": "second click"},
        )
    )
    assert out["already_archived"] is True

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "navigator.tool.archive_bucket_article",
                AuditLog.target_id == str(article.id),
            )
        )
    ).scalars().all()
    assert audit == []


@pytest.mark.asyncio
async def test_archive_article_rejects_empty_reason(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    bucket = await _make_bucket(db_session, workspace_id=ws.id)
    article = await _make_article(db_session, bucket)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "archive_bucket_article",
            {"article_id": str(article.id), "reason": "   "},
        )
    )
    assert out["error"] == "validation_failed"

    fresh = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.id == article.id)
        )
    ).scalar_one()
    assert fresh.archived_at is None


@pytest.mark.asyncio
async def test_archive_article_404_for_other_workspaces_article(
    db_session, seed_workspace
) -> None:
    """Two real workspaces: the article lives in workspace B, the
    toolbox is bound to workspace A. The fence in
    ``_tool_archive_bucket_article`` joins on ``workspace_id`` so the
    cross-tenant id is rejected with ``not_found``."""
    from backend.app.db.models.tenancy import Workspace

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    bucket_b = await _make_bucket(
        db_session, workspace_id=ws_b.id, slug="ws-b-bucket"
    )
    article_b = await _make_article(db_session, bucket_b)

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "archive_bucket_article",
            {"article_id": str(article_b.id), "reason": "trying"},
        )
    )
    assert out["error"] == "not_found"


@pytest.mark.asyncio
async def test_archive_article_admin_gated(db_session, seed_workspace) -> None:
    """A member-role caller (i.e. user_id NOT in admin/owner) gets a
    forbidden error before the row is touched."""
    user, _, ws = seed_workspace
    bucket = await _make_bucket(db_session, workspace_id=ws.id)
    article = await _make_article(db_session, bucket)

    # Build a second user with role=member; box is bound to *that*
    # user_id so the admin gate denies.
    from backend.app.db.models.tenancy import User, WorkspaceMember

    member = User(
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Member",
    )
    db_session.add(member)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=member.id, role="member"
        )
    )
    await db_session.flush()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "archive_bucket_article",
            {"article_id": str(article.id), "reason": "trying"},
        )
    )
    assert out["error"] == "forbidden"

    fresh = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.id == article.id)
        )
    ).scalar_one()
    assert fresh.archived_at is None
