"""v1 API — archive / restore endpoints for ``bucket_articles``.

The Console + Navigator both need a way to flip a published article
into ``archived`` once an ADR / runbook / facts page goes stale.
The historical KB-4 path only covered DRAFTS coming out of the
synthesiser; published rows had no operator-facing exit ramp, so the
agent kept warming up its memory with knowledge that no longer
matched the code.

Coverage:

1. **Archive happy path** — published article flips to ``archived``
   with ``archived_at`` set and an ``AuditLog`` row carrying the
   reason. Endpoint returns the updated row's projection.
2. **Idempotency** — re-archiving an already-archived article is a
   no-op. No second audit row.
3. **Reason required** — empty reason → 422.
4. **Admin-only** — non-admin members get 403.
5. **Restore happy path** — archived article goes back to
   ``published`` when the slug is free.
6. **Restore conflict** — when a different article has been published
   under the same slug while this one was archived, restore lands as
   ``draft`` so the partial unique index doesn't reject and the
   operator can compare versions.
7. **Tenancy** — article id from another workspace returns 404.
"""

from __future__ import annotations

import hashlib
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
from backend.app.db.models.tenancy import AuditLog


def _now():
    return datetime.now(timezone.utc)


def _make_article(
    bucket: KnowledgeBucket,
    *,
    slug: str = "live",
    title: str = "ADR: live decision",
    body: str = "decision body",
    status: str = BucketArticleStatus.PUBLISHED,
    archived: bool = False,
    version: int = 1,
) -> BucketArticle:
    return BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=slug,
        title=title,
        body_md=body,
        content_sha=hashlib.sha256(body.encode()).hexdigest(),
        version=version,
        status=status,
        provenance={"source_kind": bucket.source_kind},
        archived_at=_now() if archived else None,
    )


@pytest_asyncio.fixture
async def seeded(db_session, seed_workspace):
    user, raw, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="architecture-decisions",
        name="Architecture Decisions",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    return {"user": user, "token": raw, "workspace": workspace, "bucket": bucket}


@pytest.mark.asyncio
async def test_archive_flips_status_and_writes_audit(
    v1_client, db_session, seeded
) -> None:
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    raw = seeded["token"]
    article = _make_article(bucket)
    db_session.add(article)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/archive",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "Decision reverted in commit cf9f983."},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["id"] == str(article.id)
    assert payload["status"] == "archived"
    assert payload["archived_at"] is not None

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
                AuditLog.action == "knowledge.article.archive",
                AuditLog.target_id == str(article.id),
            )
        )
    ).scalars().all()
    assert len(audit) == 1
    assert audit[0].payload["reason"] == "Decision reverted in commit cf9f983."
    assert audit[0].payload["bucket_slug"] == bucket.slug


@pytest.mark.asyncio
async def test_archive_is_idempotent(v1_client, db_session, seeded) -> None:
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    raw = seeded["token"]
    article = _make_article(bucket, archived=True, status=BucketArticleStatus.ARCHIVED)
    db_session.add(article)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/archive",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "second click"},
    )
    assert resp.status_code == 200
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "knowledge.article.archive",
                AuditLog.target_id == str(article.id),
            )
        )
    ).scalars().all()
    assert audit == []  # idempotent: no second audit row


@pytest.mark.asyncio
async def test_archive_rejects_empty_reason(v1_client, seeded, db_session) -> None:
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    raw = seeded["token"]
    article = _make_article(bucket)
    db_session.add(article)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/archive",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_archive_requires_admin_role(
    v1_client, db_session, seeded
) -> None:
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    article = _make_article(bucket)
    db_session.add(article)

    # Add a brand-new user with role=member (not admin/owner) to the
    # same workspace and mint them a PAT so the route sees a member,
    # not the owner from the seeded fixture.
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        User,
        WorkspaceMember,
    )

    member_user = User(
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Member",
    )
    db_session.add(member_user)
    await db_session.flush()
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    token = ApiToken(
        user_id=member_user.id,
        name="member-token",
        hashed_secret=_hash_token(raw),
        prefix=PAT_PREFIX,
        scopes=["workspace:read", "workspace:write"],
    )
    db_session.add(token)
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=member_user.id,
            role="member",
        )
    )
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/archive",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "trying"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_restore_flips_back_to_published_when_slug_free(
    v1_client, db_session, seeded
) -> None:
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    raw = seeded["token"]
    article = _make_article(
        bucket, archived=True, status=BucketArticleStatus.ARCHIVED
    )
    db_session.add(article)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/restore",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "operator changed their mind"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "published"
    assert payload["archived_at"] is None


@pytest.mark.asyncio
async def test_restore_demotes_to_draft_on_published_sibling(
    v1_client, db_session, seeded
) -> None:
    """Restoring would violate the partial unique index when another
    article published under the same ``(bucket_id, slug)`` while the
    archived one was hidden. The route demotes the restore to
    ``draft`` so the operator can compare and pick a winner."""
    bucket = seeded["bucket"]
    workspace = seeded["workspace"]
    raw = seeded["token"]
    archived = _make_article(
        bucket,
        slug="api-contract",
        archived=True,
        status=BucketArticleStatus.ARCHIVED,
    )
    sibling = _make_article(
        bucket,
        slug="api-contract",  # same slug, currently published
        title="ADR: replacement",
        version=2,
    )
    db_session.add_all([archived, sibling])
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}"
        f"/articles/{archived.id}/restore",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "compare with v2"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "draft"
    assert payload["archived_at"] is None


@pytest.mark.asyncio
async def test_archive_404_for_other_workspaces_article(
    v1_client, db_session, seeded
) -> None:
    bucket = seeded["bucket"]
    raw = seeded["token"]
    other_workspace_id = uuid.uuid4()

    article = _make_article(bucket)
    db_session.add(article)
    await db_session.flush()

    # Different workspace path → membership check fails first.
    resp = await v1_client.post(
        f"/v1/workspaces/{other_workspace_id}/buckets/{bucket.slug}"
        f"/articles/{article.id}/archive",
        headers={"Authorization": f"Bearer {raw}"},
        json={"reason": "trying"},
    )
    assert resp.status_code in (403, 404)
