"""Agent tool cutover: Phase 5d moves knowledge-bucket reads to articles.

Three tools changed:

1. :meth:`ToolBox._tool_search_buckets` — was cosine-search over
   ``bucket_summaries``; now ranks ``bucket_articles`` with the same
   semantic (published + unarchived + embedded + agent_memory scope).
2. :meth:`ToolBox._tool_get_knowledge_bucket` — was reading
   ``bucket_summaries``; now reads ``bucket_articles`` and exposes
   both ``summaries`` (backward-compat) and ``articles`` (canonical)
   in the response.
3. :meth:`ToolBox._tool_list_buckets` — ``summary_count`` now reflects
   published-article count. For agent-memory buckets this is 1:1 with
   the old count thanks to Phase 5b dual-write; for repo_files buckets
   it surfaces the mirrored file count instead of a useless 0.

We stub :func:`embed_text` with a hash-seeded one-hot vector (same
trick as ``test_topic_retrieve_from_articles.py``) so tests run
offline and deterministic.
"""

from __future__ import annotations

import hashlib
import json
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
from backend.app.services.agent.tools import ToolBox


_EMBED_DIM = 1536


def _stub_vec(seed: str) -> list[float]:
    idx = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big") % _EMBED_DIM
    vec = [0.0] * _EMBED_DIM
    vec[idx] = 1.0
    return vec


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _make_article(
    bucket: KnowledgeBucket,
    *,
    title: str,
    body: str,
    embed_seed: str | None = None,
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
        embedding=_stub_vec(embed_seed) if embed_seed else None,
        archived_at=(None if not archived else _now()),
    )


async def _seed_bucket(
    db_session,
    workspace,
    *,
    slug: str,
    source_kind: str = BucketSource.AGENT_MEMORY,
    archived: bool = False,
) -> KnowledgeBucket:
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug=slug,
        name=slug.title(),
        scope_kind=BucketScope.WORKSPACE,
        source_kind=source_kind,
        archived_at=(_now() if archived else None),
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


@pytest_asyncio.fixture
async def toolbox(db_session, seed_workspace, monkeypatch):
    _, _, workspace = seed_workspace

    async def _fake_embed(text, *, settings):  # type: ignore[unused-argument]
        return _stub_vec(text)

    import backend.app.services.agent.tools as tools_mod

    monkeypatch.setattr(tools_mod, "embed_text", _fake_embed)

    box = ToolBox(
        db_session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),
    )
    return box, workspace


@pytest.mark.asyncio
async def test_search_buckets_reads_from_articles(
    db_session, toolbox
) -> None:
    """search_buckets returns a hit when only the article exists.

    Proves the cutover: no :class:`BucketSummary` row is seeded, yet
    the tool still finds the bucket because it now indexes the article
    embedding.
    """
    box, workspace = toolbox
    bucket = await _seed_bucket(db_session, workspace, slug="auth")
    db_session.add(
        _make_article(
            bucket,
            title="Auth notes",
            body="Refresh tokens moved to the edge.",
            embed_seed="auth refactor",
        )
    )
    await db_session.flush()

    result = json.loads(await box.invoke("search_buckets", {"query": "auth refactor"}))
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["bucket_slug"] == "auth"
    assert hit["title"] == "Auth notes"
    assert "Refresh tokens" in hit["summary"]
    assert hit["similarity"] > 0.9


@pytest.mark.asyncio
async def test_search_buckets_filters_repo_files_source(
    db_session, toolbox
) -> None:
    """Scope guard: repo_files articles never surface in search_buckets.

    Matches :meth:`TopicService.retrieve_buckets` (Phase 5c). The
    tool's spec description frames this as "prior conversations", so
    broadening to repo docs would change the LLM's mental model.
    """
    box, workspace = toolbox
    repo_bucket = await _seed_bucket(
        db_session, workspace, slug="kb", source_kind=BucketSource.REPO_FILES
    )
    db_session.add(
        _make_article(
            repo_bucket,
            title="Ship KB",
            body="Code style.",
            embed_seed="auth refactor",
        )
    )
    await db_session.flush()

    result = json.loads(await box.invoke("search_buckets", {"query": "auth refactor"}))
    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_buckets_ignores_superseded_and_archived(
    db_session, toolbox
) -> None:
    box, workspace = toolbox
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
            title="live-but-archived",
            body="deleted",
            embed_seed="q",
            archived=True,
        )
    )
    await db_session.flush()

    result = json.loads(await box.invoke("search_buckets", {"query": "q"}))
    assert result["results"] == []


@pytest.mark.asyncio
async def test_get_knowledge_bucket_returns_articles_and_summaries(
    db_session, toolbox
) -> None:
    """Tool response carries both ``summaries`` (legacy key) and ``articles``.

    Keeping ``summaries`` means any system-prompt guidance the agent
    already internalised keeps working; exposing ``articles`` primes
    the Phase 4 UI and future tool-spec refresh.
    """
    box, workspace = toolbox
    bucket = await _seed_bucket(db_session, workspace, slug="platform")
    db_session.add(_make_article(bucket, title="ADR: routing", body="..."))
    db_session.add(_make_article(bucket, title="Onboarding", body="..."))
    await db_session.flush()

    raw = await box.invoke(
        "get_knowledge_bucket", {"slug": "platform", "include_summaries": True}
    )
    out = json.loads(raw)
    assert out["slug"] == "platform"
    assert out["source_kind"] == "agent_memory"
    assert len(out["summaries"]) == 2
    assert out["summaries"] == out["articles"]  # same payload, two keys
    # Every article has the new canonical fields without breaking the
    # legacy shape (``id``, ``title``, ``summary``, ``created_at``).
    for row in out["articles"]:
        assert "id" in row
        assert "title" in row
        assert "body_md" in row
        assert "version" in row
        assert row["summary"] == row["body_md"]


@pytest.mark.asyncio
async def test_get_knowledge_bucket_skips_superseded(
    db_session, toolbox
) -> None:
    """Version history stays hidden from the default bucket detail call."""
    box, workspace = toolbox
    bucket = await _seed_bucket(db_session, workspace, slug="a")
    db_session.add(
        _make_article(
            bucket,
            title="v1",
            body="old",
            status=BucketArticleStatus.SUPERSEDED,
        )
    )
    db_session.add(_make_article(bucket, title="v2", body="new"))
    await db_session.flush()

    out = json.loads(await box.invoke("get_knowledge_bucket", {"slug": "a"}))
    titles = {s["title"] for s in out["articles"]}
    assert titles == {"v2"}


@pytest.mark.asyncio
async def test_list_buckets_summary_count_reflects_articles(
    db_session, toolbox
) -> None:
    """``list_buckets.summary_count`` is now the published article count.

    Same JSON key, new semantics. For agent_memory buckets the count
    matches the legacy summary count because of the Phase 5b dual-write;
    this test asserts the count tracks articles directly so a future
    source that doesn't emit summaries (e.g. repo_files, audio) still
    reports the right number.
    """
    box, workspace = toolbox
    bucket = await _seed_bucket(db_session, workspace, slug="platform")
    db_session.add(_make_article(bucket, title="a", body="x"))
    db_session.add(_make_article(bucket, title="b", body="y"))
    db_session.add(
        # Superseded/archived rows don't count — mirrors the UI filter.
        _make_article(
            bucket, title="old", body="z", status=BucketArticleStatus.SUPERSEDED
        )
    )
    await db_session.flush()

    out = json.loads(await box.invoke("list_buckets", {}))
    [row] = [b for b in out["buckets"] if b["slug"] == "platform"]
    assert row["summary_count"] == 2
    assert row["article_count"] == 2
    assert row["source_kind"] == "agent_memory"


@pytest.mark.asyncio
async def test_list_buckets_hides_repo_files_articles(
    db_session, toolbox
) -> None:
    """repo_files buckets are hidden after the DB-only knowledge cutover."""
    box, workspace = toolbox
    repo_bucket = await _seed_bucket(
        db_session, workspace, slug="kb", source_kind=BucketSource.REPO_FILES
    )
    db_session.add(_make_article(repo_bucket, title="style.md", body="..."))
    await db_session.flush()

    out = json.loads(await box.invoke("list_buckets", {}))
    assert [b for b in out["buckets"] if b["slug"] == "kb"] == []
