"""Tests for :mod:`backend.app.services.bucket_summary_articles`.

Phase 5b: ``BucketSummary`` → ``BucketArticle`` mirror. Exercises:

1. **Single mirror** — one summary → one article with expected slug,
   title, body, embedding carry-over, and ``source_kind='agent_memory'``
   provenance.
2. **Idempotency** — running the mirror twice for the same summary is
   a no-op (doesn't insert a duplicate, doesn't raise).
3. **Bulk backfill** — seeding several summaries across multiple
   buckets and calling ``backfill_missing_articles_for_workspace``
   produces one article per summary and is re-callable.
4. **Scoping** — the workspace-level backfill doesn't touch summaries
   in other workspaces (tenancy).
5. **NULL tolerance** — ``thread_id`` / ``created_by_user_id`` may be
   NULL on the summary; provenance captures them as JSON null.
6. **Slug stability** — :func:`article_slug_for_summary` is a pure
   function of the summary UUID; regression guard against accidental
   drift.
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
    BucketSummary,
    KnowledgeBucket,
)
from backend.app.services.bucket_summary_articles import (
    article_slug_for_summary,
    backfill_missing_articles_for_bucket,
    backfill_missing_articles_for_workspace,
    mirror_summary_to_article,
)


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


async def _load_articles(db_session, *, bucket_id) -> list[BucketArticle]:
    rows = (
        await db_session.execute(
            select(BucketArticle)
            .where(BucketArticle.bucket_id == bucket_id)
            .order_by(BucketArticle.created_at, BucketArticle.slug)
        )
    ).scalars().all()
    return list(rows)


def _summary(
    bucket: KnowledgeBucket,
    *,
    title: str = "Topic",
    body: str = "Summary body.",
    thread_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    embedding: list[float] | None = None,
) -> BucketSummary:
    s = BucketSummary(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        thread_id=thread_id,
        title=title,
        summary=body,
        embedding=embedding,
        created_by_user_id=user_id,
    )
    return s


@pytest.mark.asyncio
async def test_slug_is_deterministic_and_collision_free() -> None:
    """Slug is a pure hex function of the UUID — no hashing / salting."""
    a = uuid.UUID("00000000-0000-0000-0000-000000000001")
    b = uuid.UUID("00000000-0000-0000-0000-000000000002")
    assert article_slug_for_summary(a) == (
        "thread-00000000000000000000000000000001"
    )
    assert article_slug_for_summary(b) == (
        "thread-00000000000000000000000000000002"
    )
    # No dashes; stays under slug(120) with room to spare.
    assert "-" not in article_slug_for_summary(a)[len("thread-"):]


@pytest.mark.asyncio
async def test_single_mirror_creates_article_with_expected_projection(
    db_session, seeded_bucket
) -> None:
    """Covers title/body copy, embedding carry-over, and provenance shape.

    This is the contract Phase 5c will read against — if any of these
    fields drift the read-path cutover will blow up silently, so we
    pin them explicitly.
    """
    workspace, bucket = seeded_bucket
    # thread_id stays NULL here — exercising the FK would require a
    # full ChatThread fixture; ``test_pack_topic_dual_writes_article``
    # below covers the non-null branch end-to-end.
    s = _summary(
        bucket,
        title="Auth refactor v1",
        body="We moved refresh-token issuance into the edge middleware.",
        embedding=[0.1] * 1536,
    )
    db_session.add(s)
    await db_session.flush()

    article = await mirror_summary_to_article(db_session, s)
    assert article is not None
    await db_session.flush()

    rows = await _load_articles(db_session, bucket_id=bucket.id)
    assert len(rows) == 1
    a = rows[0]
    assert a.slug == article_slug_for_summary(s.id)
    assert a.title == "Auth refactor v1"
    assert a.body_md == s.summary
    assert a.version == 1
    assert a.status == BucketArticleStatus.PUBLISHED
    assert a.supersedes_id is None
    # embedding is carried over wholesale; checking the first sample
    # is enough — pgvector round-trips as a list[float].
    assert a.embedding is not None
    assert pytest.approx(a.embedding[0]) == 0.1

    prov = a.provenance
    assert prov["source_kind"] == BucketSource.AGENT_MEMORY
    assert prov["summary_id"] == str(s.id)
    assert prov["thread_id"] is None  # populated in the pack_topic test
    assert prov["created_by_user_id"] is None
    assert prov["packed_at"] is not None


@pytest.mark.asyncio
async def test_mirror_is_idempotent(db_session, seeded_bucket) -> None:
    """Calling the mirror twice never produces two articles.

    Guards the race between the pack_topic dual-write and the data
    migration / CLI reconcile — both paths use the same deterministic
    slug and both must no-op when the other already landed.
    """
    _, bucket = seeded_bucket
    s = _summary(bucket)
    db_session.add(s)
    await db_session.flush()

    first = await mirror_summary_to_article(db_session, s)
    assert first is not None
    await db_session.flush()

    second = await mirror_summary_to_article(db_session, s)
    assert second is None

    rows = await _load_articles(db_session, bucket_id=bucket.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_mirror_tolerates_null_thread_and_user(
    db_session, seeded_bucket
) -> None:
    """NULL FKs don't blow up — provenance captures them as JSON null.

    The schema allows ``thread_id`` / ``created_by_user_id`` to be NULL
    (thread hard-deleted, summary imported from pre-auth era). Mirror
    must preserve those as JSON null, not the string ``"None"``.
    """
    _, bucket = seeded_bucket
    s = _summary(bucket, thread_id=None, user_id=None)
    db_session.add(s)
    await db_session.flush()

    article = await mirror_summary_to_article(db_session, s)
    assert article is not None
    await db_session.flush()

    prov = article.provenance
    assert prov["thread_id"] is None
    assert prov["created_by_user_id"] is None


@pytest.mark.asyncio
async def test_workspace_backfill_creates_one_article_per_summary(
    db_session, seed_workspace
) -> None:
    """Bulk path handles multiple buckets + multiple summaries per bucket.

    Captures the realistic state after a tenant has accumulated a mix
    of pack runs before Phase 5b ships.
    """
    _, _, workspace = seed_workspace

    b1 = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="b1",
        name="B1",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    b2 = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="b2",
        name="B2",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add_all([b1, b2])
    await db_session.flush()

    summaries = [
        _summary(b1, title="t1", body="body-1"),
        _summary(b1, title="t2", body="body-2"),
        _summary(b2, title="t3", body="body-3"),
    ]
    for s in summaries:
        db_session.add(s)
    await db_session.flush()

    report = await backfill_missing_articles_for_workspace(
        db_session, workspace.id
    )
    await db_session.flush()

    assert report.articles_created == 3
    assert report.summaries_scanned == 3
    assert report.summaries_skipped_existing == 0

    assert len(await _load_articles(db_session, bucket_id=b1.id)) == 2
    assert len(await _load_articles(db_session, bucket_id=b2.id)) == 1


@pytest.mark.asyncio
async def test_workspace_backfill_is_idempotent(
    db_session, seeded_bucket
) -> None:
    """Re-running after a mixed partial run is a no-op on the existing rows."""
    _, bucket = seeded_bucket
    s = _summary(bucket)
    db_session.add(s)
    await db_session.flush()

    first = await backfill_missing_articles_for_workspace(
        db_session, bucket.workspace_id
    )
    assert first.articles_created == 1

    second = await backfill_missing_articles_for_workspace(
        db_session, bucket.workspace_id
    )
    assert second.articles_created == 0
    assert second.summaries_skipped_existing == 1

    rows = await _load_articles(db_session, bucket_id=bucket.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_backfill_respects_workspace_boundary(
    db_session, seed_workspace
) -> None:
    """Summaries in another workspace stay untouched by this one's backfill.

    Multi-tenant regression guard — a ``backfill`` run for tenant A
    must never mirror tenant B's packed summaries (the deterministic
    slug would technically accept the insert since slugs are scoped
    per bucket, but scanning rows across workspace boundaries would
    still be a privacy violation).
    """
    from backend.app.db.models.tenancy import Workspace

    _, _, workspace_a = seed_workspace

    # Second workspace inside the same org. Using the same org avoids
    # needing to build a fresh Org fixture; the tenancy layer we're
    # exercising is workspace_id, not org_id.
    workspace_b = Workspace(
        id=uuid.uuid4(),
        org_id=workspace_a.org_id,
        slug="other-ws",
        name="Other Workspace",
    )
    db_session.add(workspace_b)
    await db_session.flush()

    bucket_b = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_b.id,
        slug="shared-slug",
        name="B",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket_b)
    await db_session.flush()

    s_b = _summary(bucket_b, body="should stay orphaned")
    db_session.add(s_b)
    await db_session.flush()

    report = await backfill_missing_articles_for_workspace(
        db_session, workspace_a.id
    )
    assert report.summaries_scanned == 0
    assert report.articles_created == 0

    other_rows = await _load_articles(db_session, bucket_id=bucket_b.id)
    assert other_rows == []


@pytest.mark.asyncio
async def test_bucket_backfill_scopes_to_one_bucket(
    db_session, seed_workspace
) -> None:
    """Per-bucket backfill ignores summaries in other buckets in the same ws."""
    _, _, workspace = seed_workspace
    b1 = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="a",
        name="A",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    b2 = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="b",
        name="B",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add_all([b1, b2])
    await db_session.flush()

    db_session.add(_summary(b1))
    db_session.add(_summary(b2))
    await db_session.flush()

    created = await backfill_missing_articles_for_bucket(db_session, b1)
    await db_session.flush()

    assert created == 1
    assert len(await _load_articles(db_session, bucket_id=b1.id)) == 1
    # Other bucket untouched.
    assert await _load_articles(db_session, bucket_id=b2.id) == []


@pytest.mark.asyncio
async def test_pack_topic_dual_writes_article(
    db_session, seed_workspace, seed_user_with_token
) -> None:
    """``pack_topic`` creates both a summary and its article mirror.

    The service's dual-write happens before ``session.flush()``, so a
    single commit produces a ``BucketSummary`` + matching
    ``BucketArticle`` with the deterministic slug. Protects the happy
    path from regressing when ``topic.py`` is refactored.
    """
    from backend.app.db.models.agent_surface import (
        ChatMessage as ChatMessageRow,
    )
    from backend.app.db.models.agent_surface import ChatThread
    from backend.app.services.agent.topic import TopicService

    _, _, workspace = seed_workspace

    thread = ChatThread(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        title="Auth",
    )
    db_session.add(thread)
    await db_session.flush()

    db_session.add_all(
        [
            ChatMessageRow(
                thread_id=thread.id,
                role="user",
                body="please help with auth",
            ),
            ChatMessageRow(
                thread_id=thread.id,
                role="assistant",
                body="sure, here's the plan",
            ),
        ]
    )
    await db_session.flush()

    # Stub the LLM + embedder so the test doesn't try to dial out.
    user, _ = seed_user_with_token
    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type] — not used on stubbed paths
        client=None,  # type: ignore[arg-type] — ditto
        workspace_id=workspace.id,
        user_id=user.id,
    )

    async def _fake_summarise(_messages):  # type: ignore[no-redef]
        return ("Packed auth discussion", "Agreed on the middleware plan.")

    async def _fake_embed(_text, *, settings):  # type: ignore[no-redef]
        return [0.2] * 1536

    service._summarise_thread = _fake_summarise  # type: ignore[method-assign]

    import backend.app.services.agent.topic as topic_mod

    original_embed = topic_mod.embed_text
    topic_mod.embed_text = _fake_embed  # type: ignore[assignment]
    try:
        summary = await service.pack_topic(
            thread,
            bucket_slug="auth-refactor",
            bucket_name="Auth refactor",
        )
    finally:
        topic_mod.embed_text = original_embed

    await db_session.flush()

    articles = await _load_articles(db_session, bucket_id=summary.bucket_id)
    assert len(articles) == 1
    a = articles[0]
    assert a.slug == article_slug_for_summary(summary.id)
    assert a.title == "Packed auth discussion"
    assert a.body_md == "Agreed on the middleware plan."
    assert a.provenance["summary_id"] == str(summary.id)
    assert a.provenance["thread_id"] == str(thread.id)
