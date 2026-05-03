"""HTTP tests for workspace knowledge search.

Covers the vector-search re-ranking contract (repo_match → workspace →
other_repo) and the 412 fall-back when embeddings aren't configured.

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
    # PR-7C extracted the embedding call into
    # ``backend.app.services.knowledge_search``; the HTTP route now
    # delegates to that module, so tests patch there.
    import backend.app.services.knowledge_search as mod

    monkeypatch.setattr(mod, "embed_text", embedder)


@pytest.mark.asyncio
async def test_search_returns_workspace_hits_only_even_with_repo_hint(
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
    assert [hit["rank_bucket"] for hit in body["hits"]] == ["workspace"]
    assert body["hits"][0]["repo_id"] is None


@pytest.mark.asyncio
async def test_search_ranks_workspace_first_without_repo_match(
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

    assert rank_buckets == ["workspace"]


@pytest.mark.asyncio
async def test_search_returns_412_if_embeddings_unconfigured(
    v1_client, seed_search_workspace, monkeypatch
) -> None:
    ctx = seed_search_workspace
    headers = {"Authorization": f"Bearer {ctx['raw']}"}

    import backend.app.services.knowledge_search as mod

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


