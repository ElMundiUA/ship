"""Tests for :meth:`TopicService.retrieve_buckets` after Phase 5c cutover.

The retriever now reads from :class:`BucketArticle` instead of
:class:`BucketSummary`. Behavioural invariants we pin:

1. **Articles are the source of truth** — a bucket that has both a
   summary and its mirror article still retrieves via the article's
   row (exercised indirectly: we delete the summary row but keep the
   article, and the retriever still finds it).
2. **Scope stays agent-memory only** — ``repo_files`` articles don't
   pollute the "warmed memory" prompt section, even if they happen to
   have an embedding.
3. **Only published + unarchived articles** — superseded / archived
   history from Phase 5a's versioning doesn't leak.
4. **Similarity filter works** — an unrelated article with distant
   embedding is dropped by ``similarity_threshold``.
5. **BucketHit.article_id points at the actual article row**, not at
   the legacy summary; ``summary`` holds ``article.body_md``.

The embedding stub returns a fixed vector based on a hash of the
string so the test is deterministic without hitting the OpenAI API.
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
from backend.app.services.agent.topic import TopicService


_EMBED_DIM = 1536


def _stub_vec(seed: str) -> list[float]:
    """Deterministic direction-like vector for cosine math in tests.

    The retriever uses cosine distance, so what matters is the
    *direction* not the magnitude. We build a sparse-one-hot vector
    whose active index is picked from a hash of the seed; two seeds
    with the same active index cosine to ~1.0, different indices
    cosine to 0.0.
    """
    idx = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big") % _EMBED_DIM
    vec = [0.0] * _EMBED_DIM
    vec[idx] = 1.0
    return vec


def _make_article(
    bucket: KnowledgeBucket,
    *,
    title: str,
    body: str,
    embed_seed: str,
    status: str = BucketArticleStatus.PUBLISHED,
    archived: bool = False,
    slug: str | None = None,
) -> BucketArticle:
    aid = uuid.uuid4()
    return BucketArticle(
        id=aid,
        bucket_id=bucket.id,
        slug=slug or f"a-{aid.hex[:8]}",
        title=title,
        body_md=body,
        content_sha=hashlib.sha256(body.encode()).hexdigest(),
        version=1,
        status=status,
        supersedes_id=None,
        provenance={"source_kind": bucket.source_kind},
        embedding=_stub_vec(embed_seed),
        archived_at=(None if not archived else _now()),
    )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def topic_service_with_buckets(
    db_session, seed_workspace, monkeypatch
):
    """Build a :class:`TopicService` with the embedder stubbed out."""
    _, _, workspace = seed_workspace

    # retrieve_buckets calls ``embed_text`` from the module namespace;
    # swap it for a hash-seeded stub so the test is deterministic and
    # offline. We match ``retrieve_buckets``' call signature.
    async def _fake_embed(text, *, settings):  # type: ignore[unused-argument]
        return _stub_vec(text)

    import backend.app.services.agent.topic as topic_mod

    monkeypatch.setattr(topic_mod, "embed_text", _fake_embed)

    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
    )
    return service, workspace


async def _seed_bucket(
    db_session, workspace, *, slug: str, source_kind: str = BucketSource.AGENT_MEMORY
) -> KnowledgeBucket:
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug=slug,
        name=slug.title(),
        scope_kind=BucketScope.WORKSPACE,
        source_kind=source_kind,
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


@pytest.mark.asyncio
async def test_retrieve_buckets_reads_from_articles_not_summaries(
    db_session, topic_service_with_buckets
) -> None:
    """Cutover guard: only the article row exists, retriever still matches.

    Seeds an article with a matching embedding direction and asserts
    the retriever returns it — without any :class:`BucketSummary` row
    backing it. Proves the read path has fully moved.
    """
    service, workspace = topic_service_with_buckets
    bucket = await _seed_bucket(db_session, workspace, slug="auth")

    article = _make_article(
        bucket,
        title="Auth refactor notes",
        body="We moved refresh-token issuance into the edge middleware.",
        embed_seed="auth refactor",
    )
    db_session.add(article)
    await db_session.flush()

    hits = await service.retrieve_buckets(query="auth refactor")
    assert len(hits) == 1
    h = hits[0]
    assert h.article_id == article.id
    assert h.bucket_slug == "auth"
    assert h.title == "Auth refactor notes"
    assert h.summary == article.body_md
    assert h.similarity > 0.9  # same embed seed → same one-hot vector


@pytest.mark.asyncio
async def test_retrieve_buckets_skips_repo_files_source(
    db_session, topic_service_with_buckets
) -> None:
    """repo_files articles never feed the warmed-memory prompt section.

    Retrieval scope is ``agent_memory`` only — a repo_files article
    with a perfectly matching embedding must still be filtered out.
    """
    service, workspace = topic_service_with_buckets
    repo_bucket = await _seed_bucket(
        db_session, workspace, slug="kb", source_kind=BucketSource.REPO_FILES
    )
    db_session.add(
        _make_article(
            repo_bucket,
            title="Ship knowledge",
            body="Code style guide.",
            embed_seed="auth refactor",  # same seed as query!
        )
    )
    await db_session.flush()

    hits = await service.retrieve_buckets(query="auth refactor")
    assert hits == []


@pytest.mark.asyncio
async def test_retrieve_buckets_filters_superseded_and_archived(
    db_session, topic_service_with_buckets
) -> None:
    """Superseded history and archived-bucket rows stay out of retrieval.

    Guards the read path against Phase 5a's version history leaking:
    when an agent-memory bucket is archived, or when a future write
    path leaves a superseded row behind, it must not re-surface.
    """
    service, workspace = topic_service_with_buckets
    bucket = await _seed_bucket(db_session, workspace, slug="a")

    db_session.add(
        _make_article(
            bucket,
            title="old",
            body="stale",
            embed_seed="q",
            status=BucketArticleStatus.SUPERSEDED,
        )
    )
    db_session.add(
        _make_article(
            bucket,
            title="gone",
            body="gone",
            embed_seed="q",
            archived=True,
        )
    )
    await db_session.flush()

    hits = await service.retrieve_buckets(query="q")
    assert hits == []

    # Archive the bucket; a published article inside it should still
    # be filtered out (bucket-level ``archived_at`` tombstone).
    bucket.archived_at = _now()
    published = _make_article(
        bucket,
        title="live but orphaned",
        body="hidden",
        embed_seed="q",
        slug="live",
    )
    db_session.add(published)
    await db_session.flush()

    hits = await service.retrieve_buckets(query="q")
    assert hits == []


@pytest.mark.asyncio
async def test_retrieve_buckets_enforces_similarity_threshold(
    db_session, topic_service_with_buckets
) -> None:
    """Unrelated embeddings are dropped before they hit the prompt."""
    service, workspace = topic_service_with_buckets
    bucket = await _seed_bucket(db_session, workspace, slug="topic")

    db_session.add(
        _make_article(
            bucket,
            title="completely different",
            body="orthogonal content",
            embed_seed="unrelated-token",
        )
    )
    await db_session.flush()

    # Stub embedder produces a one-hot at a different index for a
    # different query string, so cosine similarity ≈ 0.
    hits = await service.retrieve_buckets(query="question about X")
    assert hits == []


@pytest.mark.asyncio
async def test_retrieve_buckets_ignores_articles_without_embedding(
    db_session, topic_service_with_buckets
) -> None:
    """Articles without an embedding are skipped, not erroring out.

    Belt-and-braces for Phase 5a repo_files articles (scope is already
    restricted to agent_memory) and for any future agent_memory write
    path that defers embedding — retriever must degrade gracefully
    instead of blowing up at cosine_distance time.
    """
    service, workspace = topic_service_with_buckets
    bucket = await _seed_bucket(db_session, workspace, slug="a")

    # No embedding on this article at all.
    a = _make_article(bucket, title="t", body="b", embed_seed="seed")
    a.embedding = None
    db_session.add(a)
    await db_session.flush()

    hits = await service.retrieve_buckets(query="seed")
    assert hits == []
