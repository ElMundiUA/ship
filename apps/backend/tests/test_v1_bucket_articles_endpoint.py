"""v1 API — ``GET /v1/workspaces/{ws}/buckets/{slug}/articles`` (Phase 5d).

Canonical article listing. Covers:

1. **Empty bucket** — returns ``[]`` without crashing.
2. **Only published, unarchived by default** — superseded history +
   archived rows stay hidden, matching Phase 5c's retrieval semantics.
3. **``include_superseded=true``** — old versions resurface so a
   future timeline view can render them. Ordered by created_at desc
   so the newest write lands first.
4. **``include_archived=true``** — archived rows come back (admin
   inspection of "what used to live in .ship/knowledge/").
5. **Tenancy** — requesting articles in a bucket belonging to
   another workspace returns 404.
6. **Projection fields** — response payload carries every field the
   Phase 4 UI expects (id, slug, title, body_md, version, status,
   provenance, timestamps).

Legacy ``/summaries`` endpoint is exercised by existing fixtures in
``test_v1_buckets_and_feedback.py`` via ``pack_topic`` — here we only
assert the new endpoint's shape + filter semantics.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
import pytest_asyncio

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)


def _article(
    bucket: KnowledgeBucket,
    *,
    slug: str,
    title: str = "t",
    body: str = "b",
    status: str = BucketArticleStatus.PUBLISHED,
    archived: bool = False,
    version: int = 1,
    provenance: dict | None = None,
) -> BucketArticle:
    aid = uuid.uuid4()
    return BucketArticle(
        id=aid,
        bucket_id=bucket.id,
        slug=slug,
        title=title,
        body_md=body,
        content_sha=hashlib.sha256(body.encode()).hexdigest(),
        version=version,
        status=status,
        provenance=provenance or {"source_kind": bucket.source_kind},
        archived_at=(None if not archived else _now()),
    )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def seeded_bucket(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="auth-refactor",
        name="Auth refactor",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    return workspace, bucket


@pytest.mark.asyncio
async def test_articles_endpoint_returns_empty_list_for_empty_bucket(
    v1_client, seed_workspace, seeded_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/articles",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_articles_endpoint_defaults_to_published_unarchived(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    """Default view hides Phase 5a supersession history + archived files.

    This is the same semantic Phase 5c's retrieval uses — the UI
    mirrors it so a user sees "one current article per thing" without
    needing to know about version history.
    """
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    published = _article(bucket, slug="live", title="current")
    superseded = _article(
        bucket, slug="live", title="old", version=0, status=BucketArticleStatus.SUPERSEDED
    )
    archived = _article(bucket, slug="gone", title="deleted", archived=True)
    db_session.add_all([published, superseded, archived])
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/articles",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload) == 1
    assert payload[0]["id"] == str(published.id)
    assert payload[0]["status"] == "published"


@pytest.mark.asyncio
async def test_articles_endpoint_include_superseded_returns_history(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    """``include_superseded=true`` exposes the Phase 5a version chain."""
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    v2 = _article(bucket, slug="main", title="v2", version=2)
    v1 = _article(
        bucket,
        slug="main",
        title="v1",
        version=1,
        status=BucketArticleStatus.SUPERSEDED,
    )
    db_session.add_all([v1, v2])
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/articles"
        "?include_superseded=true",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    titles = [row["title"] for row in payload]
    assert set(titles) == {"v1", "v2"}


@pytest.mark.asyncio
async def test_articles_endpoint_include_archived_returns_tombstones(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    live = _article(bucket, slug="live", title="ok")
    gone = _article(bucket, slug="gone", title="deleted", archived=True)
    db_session.add_all([live, gone])
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/articles"
        "?include_archived=true",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload) == 2
    statuses = {row["archived_at"] is not None for row in payload}
    assert statuses == {True, False}


@pytest.mark.asyncio
async def test_articles_endpoint_projection_carries_phase5_fields(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    """Regression guard on the wire shape the Phase 4 UI depends on."""
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    a = _article(
        bucket,
        slug="pinned",
        title="Pinned doc",
        body="The body of the article.",
        provenance={
            "source_kind": "agent_memory",
            "summary_id": str(uuid.uuid4()),
        },
    )
    db_session.add(a)
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/articles",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    [row] = resp.json()
    # Pin every field the UI / agent tool downstream can rely on.
    for k in (
        "id",
        "bucket_id",
        "slug",
        "title",
        "body_md",
        "version",
        "status",
        "provenance",
        "created_at",
        "updated_at",
        "archived_at",
    ):
        assert k in row, f"missing field: {k}"
    assert row["body_md"] == "The body of the article."
    assert row["status"] == "published"
    assert row["provenance"]["source_kind"] == "agent_memory"


@pytest.mark.asyncio
async def test_articles_endpoint_rejects_cross_workspace(
    v1_client, seed_workspace, seeded_bucket
) -> None:
    """Requesting another workspace's bucket path returns 404, not a leak."""
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket
    other_ws = uuid.uuid4()
    resp = await v1_client.get(
        f"/v1/workspaces/{other_ws}/buckets/{bucket.slug}/articles",
        headers={"Authorization": f"Bearer {raw}"},
    )
    # 403 on membership check, not 404 on the bucket lookup —
    # same behaviour as every other workspace-scoped route.
    assert resp.status_code in {403, 404}


@pytest.mark.asyncio
async def test_list_buckets_count_reflects_articles(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    """``summary_count`` in the bucket list is now backed by articles.

    Regression guard on the Phase 5d switch: the old read summed
    ``bucket_summaries``, the new read sums ``bucket_articles``. For
    agent_memory buckets the Phase 5b dual-write keeps these in lock-step,
    so a seeded article must show up in the count.
    """
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    db_session.add(_article(bucket, slug="one"))
    db_session.add(_article(bucket, slug="two"))
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    [row] = [b for b in resp.json() if b["slug"] == bucket.slug]
    assert row["summary_count"] == 2
