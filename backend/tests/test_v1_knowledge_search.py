"""HTTP tests for workspace knowledge search + canonical (PR-7A).

Covers the vector-search re-ranking contract (repo_match → workspace →
other_repo), the 412 fall-back when embeddings aren't configured, the
canonical-bucket listing with an ``override_count`` that reflects
``bucket_articles.overrides_workspace_article_id``, and the "orphan
slugs across ≥2 repos" discovery path that 7B's promotion flow will
consume.

Embeddings are deterministic-stubbed through ``monkeypatch`` so the
test suite stays offline — cosine distance on unit-length basis
vectors is exactly predictable, which is all we need to pin the
ranker without shipping a vector-quality evaluation into pytest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import pytest
import pytest_asyncio


EMBED_DIM = 1536


def _unit_vec(index: int) -> list[float]:
    """Basis vector e_index — cosine_distance(e_i, e_j) == 1 when
    i != j and 0 when i == j. Perfect for pinning "which seed wins
    which query" without any real embedding service."""
    v = [0.0] * EMBED_DIM
    v[index % EMBED_DIM] = 1.0
    return v


class _FakeEmbedder:
    """Callable stand-in for :func:`embed_text`.

    Maps a curated set of query strings onto deterministic basis
    vectors so the surrounding test can assert ordering without
    poking at pgvector internals. Unknown queries get a "far from
    everything" vector so the query is still valid but scores stay
    low.
    """

    def __init__(self, mapping: dict[str, int]):
        self._mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, query: str, *, settings=None) -> list[float]:
        self.calls.append(query)
        idx = self._mapping.get(query.strip())
        if idx is None:
            return _unit_vec(999)
        return _unit_vec(idx)


@pytest_asyncio.fixture
async def seed_search_workspace(db_session, seed_workspace):
    """Seed two activated repos + a mix of workspace/repo buckets.

    The per-test database fixture rolls back on teardown, so the
    articles + chunks planted here only live for the duration of the
    test that consumes the fixture.
    """
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketScope,
        BucketSource,
        KbChunk,
        KnowledgeBucket,
    )
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_700_001,
        account_login="ks",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo_a = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=50001,
        full_name="ks/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/ks/api",
        activated_at=datetime.now(timezone.utc),
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=50002,
        full_name="ks/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/ks/web",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    # Workspace-canonical bucket + article (vec idx 2).
    ws_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook",
        description="Workspace canonical auth knowledge.",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(ws_bucket)
    await db_session.flush()

    ws_article = BucketArticle(
        bucket_id=ws_bucket.id,
        slug="main",
        title="Auth runbook (workspace)",
        body_md="Workspace canonical guidance for the auth subsystem.",
        content_sha="ws" + "0" * 62,
        embedding=_unit_vec(2),
    )
    db_session.add(ws_article)
    await db_session.flush()

    # Repo-A bucket + article (vec idx 0).
    repo_a_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook (api)",
        description="Repo-specific auth knowledge.",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_a.id,
    )
    db_session.add(repo_a_bucket)
    await db_session.flush()

    repo_a_article = BucketArticle(
        bucket_id=repo_a_bucket.id,
        slug="main",
        title="Auth runbook (api)",
        body_md="Repo-A specific auth guidance for ks/api.",
        content_sha="ra" + "0" * 62,
        embedding=_unit_vec(0),
    )
    db_session.add(repo_a_article)
    await db_session.flush()

    # Repo-B bucket + article (vec idx 1).
    repo_b_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook (web)",
        description="Repo-B specific auth knowledge.",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_b.id,
    )
    db_session.add(repo_b_bucket)
    await db_session.flush()

    repo_b_article = BucketArticle(
        bucket_id=repo_b_bucket.id,
        slug="main",
        title="Auth runbook (web)",
        body_md="Repo-B specific auth guidance for ks/web.",
        content_sha="rb" + "0" * 62,
        embedding=_unit_vec(1),
    )
    db_session.add(repo_b_article)
    await db_session.flush()

    # KbChunks in each repo so we exercise the chunk half of the
    # union as well. These share the basis vectors with their parent
    # repo's article so the test can assert "hits from repo_a" via
    # either source.
    db_session.add_all(
        [
            KbChunk(
                workspace_id=workspace.id,
                repo_id=repo_a.id,
                source_path=".ship/knowledge/auth.md",
                content="Chunk of repo-A auth docs.",
                chunk_index=0,
                content_sha="cha" + "0" * 61,
                embedding=_unit_vec(0),
            ),
            KbChunk(
                workspace_id=workspace.id,
                repo_id=repo_b.id,
                source_path=".ship/knowledge/auth.md",
                content="Chunk of repo-B auth docs.",
                chunk_index=0,
                content_sha="chb" + "0" * 61,
                embedding=_unit_vec(1),
            ),
        ]
    )
    await db_session.flush()

    return {
        "raw": raw,
        "workspace": workspace,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "ws_bucket": ws_bucket,
        "ws_article": ws_article,
        "repo_a_bucket": repo_a_bucket,
        "repo_a_article": repo_a_article,
        "repo_b_bucket": repo_b_bucket,
        "repo_b_article": repo_b_article,
    }


def _patch_embedder(monkeypatch, embedder: _FakeEmbedder) -> None:
    import backend.app.api.v1.routes.knowledge as mod

    monkeypatch.setattr(mod, "embed_text", embedder)


@pytest.mark.asyncio
async def test_search_ranks_repo_match_first(
    v1_client, seed_search_workspace, monkeypatch
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    embedder = _FakeEmbedder({"auth": 0})
    _patch_embedder(monkeypatch, embedder)

    resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/search",
        headers=headers,
        json={
            "query": "auth",
            "repo_id": str(ctx["repo_a"].id),
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rank_buckets = [hit["rank_bucket"] for hit in body["hits"]]

    # Every repo_match row must come before every workspace row,
    # and every workspace row must come before every other_repo row.
    order = {"repo_match": 0, "workspace": 1, "other_repo": 2}
    for earlier, later in zip(rank_buckets, rank_buckets[1:]):
        assert order[earlier] <= order[later], rank_buckets

    # Sanity: all three bands represented, and the very first hit
    # is scoped to repo_a.
    assert body["hits"][0]["rank_bucket"] == "repo_match"
    assert body["hits"][0]["repo_id"] == str(ctx["repo_a"].id)
    assert "repo_match" in rank_buckets
    assert "workspace" in rank_buckets
    assert "other_repo" in rank_buckets


@pytest.mark.asyncio
async def test_search_ranks_workspace_second_without_repo_match(
    v1_client, seed_search_workspace, monkeypatch
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    embedder = _FakeEmbedder({"auth": 0})
    _patch_embedder(monkeypatch, embedder)

    resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/search",
        headers=headers,
        json={"query": "auth", "limit": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rank_buckets = [hit["rank_bucket"] for hit in body["hits"]]

    # With no ``repo_id`` hint the workspace band is on top.
    assert "repo_match" not in rank_buckets
    assert rank_buckets[0] == "workspace"
    # Workspace then other_repo — no interleaving.
    seen_other = False
    for b in rank_buckets:
        if b == "other_repo":
            seen_other = True
        else:
            assert not seen_other, rank_buckets


@pytest.mark.asyncio
async def test_search_returns_412_if_embeddings_unconfigured(
    v1_client, seed_search_workspace, monkeypatch
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    import backend.app.api.v1.routes.knowledge as mod

    async def _boom(_query: str, *, settings=None) -> list[float]:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(mod, "embed_text", _boom)

    resp = await v1_client.post(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/search",
        headers=headers,
        json={"query": "auth"},
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"]["code"] == "embeddings_unconfigured"


@pytest.mark.asyncio
async def test_canonical_lists_workspace_scope_buckets(
    v1_client, seed_search_workspace
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/canonical",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    canonical_ids = {b["id"] for b in body["canonical"]}
    assert str(ctx["ws_bucket"].id) in canonical_ids
    assert str(ctx["repo_a_bucket"].id) not in canonical_ids
    assert str(ctx["repo_b_bucket"].id) not in canonical_ids

    ws_entry = next(
        b for b in body["canonical"] if b["id"] == str(ctx["ws_bucket"].id)
    )
    assert ws_entry["article_count"] == 1
    assert ws_entry["override_count"] == 0


@pytest.mark.asyncio
async def test_canonical_orphan_slugs_require_two_repos(
    v1_client, seed_search_workspace, db_session
) -> None:
    from backend.app.db.models.agent_memory import (
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )

    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    # Slug present in exactly 1 repo — should NOT surface.
    db_session.add(
        KnowledgeBucket(
            workspace_id=ctx["workspace"].id,
            slug="solo-runbook",
            name="Solo runbook",
            description="Only in one repo.",
            scope_kind=BucketScope.REPO,
            source_kind=BucketSource.REPO_FILES,
            repo_id=ctx["repo_a"].id,
        )
    )
    # Slug present in two repos — SHOULD surface.
    db_session.add_all(
        [
            KnowledgeBucket(
                workspace_id=ctx["workspace"].id,
                slug="shared-runbook",
                name="Shared A",
                description=None,
                scope_kind=BucketScope.REPO,
                source_kind=BucketSource.REPO_FILES,
                repo_id=ctx["repo_a"].id,
            ),
            KnowledgeBucket(
                workspace_id=ctx["workspace"].id,
                slug="shared-runbook",
                name="Shared B",
                description=None,
                scope_kind=BucketScope.REPO,
                source_kind=BucketSource.REPO_FILES,
                repo_id=ctx["repo_b"].id,
            ),
        ]
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/canonical",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    orphan_slugs = {o["slug"] for o in body["orphan_slugs"]}
    assert "shared-runbook" in orphan_slugs
    assert "solo-runbook" not in orphan_slugs
    # ``auth-runbook`` is in 2 repos but also has a workspace-scope
    # copy, so it must NOT be reported as orphan.
    assert "auth-runbook" not in orphan_slugs

    shared = next(
        o for o in body["orphan_slugs"] if o["slug"] == "shared-runbook"
    )
    assert shared["repo_count"] == 2
    assert shared["sample_repo_full_name"] in {"ks/api", "ks/web"}


@pytest.mark.asyncio
async def test_override_link_reflected_in_override_count(
    v1_client, seed_search_workspace, db_session
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    # Wire the repo-A article as an override of the workspace article.
    ctx["repo_a_article"].overrides_workspace_article_id = ctx[
        "ws_article"
    ].id
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ctx['workspace'].id}/knowledge/canonical",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    ws_entry = next(
        b for b in body["canonical"] if b["id"] == str(ctx["ws_bucket"].id)
    )
    assert ws_entry["override_count"] == 1
