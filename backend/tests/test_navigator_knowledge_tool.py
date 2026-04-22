"""Navigator ``search_workspace_kb`` tool (PR-7C).

The tool is a thin adapter over the extracted service
(:func:`backend.app.services.knowledge_search.search_workspace_knowledge`)
but it carries a small pile of its own logic worth pinning:

1. The ``repo_match > workspace > other_repo`` ranking carries through
   the tool output so the LLM can see which band each hit came from.
2. When the LLM omits ``repo_id`` the tool substitutes the chat's
   ``active_repo_id`` (passed to :class:`ToolBox` by the agent
   runtime) so the user's current repo still wins.
3. Embedding-provider outages are surfaced as a structured
   ``{"error": "embeddings_unavailable"}`` payload instead of raised
   — the chat turn keeps going and the model can tell the user the
   feature is off.
4. The caller-provided ``limit`` is clamped at 25 so the LLM can't
   ask for a kilobyte-heavy dump.

Embeddings are stubbed with unit basis vectors (same technique as
``test_v1_knowledge_search.py``) so the tests run offline and
cosine distance is fully predictable.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


EMBED_DIM = 1536


def _unit_vec(index: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[index % EMBED_DIM] = 1.0
    return v


class _FakeEmbedder:
    """Map a curated set of queries onto deterministic basis vectors."""

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
async def seed_tool_workspace(db_session, seed_workspace):
    """Two repos + workspace-canonical + per-repo articles.

    Layout mirrors ``test_v1_knowledge_search`` so the ranker
    invariants exercised at HTTP level are also exercised through
    the tool path:

    - ``repo_a`` article embedded at basis index 0.
    - ``repo_b`` article embedded at basis index 1.
    - Workspace-canonical article at basis index 2.

    Query ``"auth"`` is mapped to basis 0 by the fake embedder in
    the tests that use it, so repo_a wins the semantic race — the
    ranker's job is to confirm the ``repo_match`` band sits on top.
    """
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, _raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_800_001,
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
        external_id=60001,
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
        external_id=60002,
        full_name="ks/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/ks/web",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

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

    repo_a_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook (api)",
        description="Repo-A auth knowledge.",
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

    repo_b_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook (web)",
        description="Repo-B auth knowledge.",
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

    return {
        "workspace": workspace,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "ws_bucket": ws_bucket,
        "repo_a_bucket": repo_a_bucket,
        "repo_b_bucket": repo_b_bucket,
    }


def _patch_embedder(monkeypatch, embedder: _FakeEmbedder) -> None:
    import backend.app.services.knowledge_search as mod

    monkeypatch.setattr(mod, "embed_text", embedder)


def _make_toolbox(
    session, *, workspace_id, active_repo_id=None
):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),
        active_repo_id=active_repo_id,
    )


@pytest.mark.asyncio
async def test_search_workspace_kb_returns_repo_match_first(
    db_session, seed_tool_workspace, monkeypatch
) -> None:
    ctx = seed_tool_workspace
    _patch_embedder(monkeypatch, _FakeEmbedder({"auth": 0}))

    box = _make_toolbox(
        db_session,
        workspace_id=ctx["workspace"].id,
        active_repo_id=ctx["repo_a"].id,
    )
    raw = await box.invoke(
        "search_workspace_kb",
        {"query": "auth", "repo_id": str(ctx["repo_a"].id), "limit": 10},
    )
    out = json.loads(raw)
    assert out["query"] == "auth"
    assert out["hits"], out
    buckets = [h["rank_bucket"] for h in out["hits"]]
    order = {"repo_match": 0, "workspace": 1, "other_repo": 2}
    for earlier, later in zip(buckets, buckets[1:]):
        assert order[earlier] <= order[later], buckets
    assert out["hits"][0]["rank_bucket"] == "repo_match"
    assert out["hits"][0]["repo"] == "ks/api"
    # All three bands represented.
    assert "repo_match" in buckets
    assert "workspace" in buckets
    assert "other_repo" in buckets
    # Scope labelling is rewritten for the LLM: bucket scope_kind
    # "repo" or "workspace" becomes a flat ``scope`` string.
    for hit in out["hits"]:
        assert hit["scope"] in {"repo", "workspace"}


@pytest.mark.asyncio
async def test_search_workspace_kb_falls_back_to_chat_repo(
    db_session, seed_tool_workspace, monkeypatch
) -> None:
    """With no ``repo_id`` arg the chat-context repo takes over.

    Query is mapped to basis 1 so repo_b wins the semantic race;
    without the fallback the ranker would promote the workspace
    article. The tool has to substitute ``active_repo_id`` into the
    service call for repo_b to land in the ``repo_match`` band.
    """
    ctx = seed_tool_workspace
    _patch_embedder(monkeypatch, _FakeEmbedder({"auth": 1}))

    box = _make_toolbox(
        db_session,
        workspace_id=ctx["workspace"].id,
        active_repo_id=ctx["repo_b"].id,
    )
    raw = await box.invoke("search_workspace_kb", {"query": "auth"})
    out = json.loads(raw)
    assert out["hits"], out
    first = out["hits"][0]
    assert first["rank_bucket"] == "repo_match"
    assert first["repo"] == "ks/web"


@pytest.mark.asyncio
async def test_search_workspace_kb_handles_embeddings_unavailable(
    db_session, seed_tool_workspace, monkeypatch
) -> None:
    """Provider outage is surfaced as structured ``error``, not raised."""
    import backend.app.services.knowledge_search as mod

    async def _boom(_query: str, *, settings=None) -> list[float]:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(mod, "embed_text", _boom)

    ctx = seed_tool_workspace
    box = _make_toolbox(db_session, workspace_id=ctx["workspace"].id)
    raw = await box.invoke("search_workspace_kb", {"query": "anything"})
    out = json.loads(raw)
    assert out["error"] == "embeddings_unavailable"
    assert "OPENAI_API_KEY" in out["message"]


@pytest.mark.asyncio
async def test_search_workspace_kb_limit_cap(
    db_session, seed_tool_workspace, monkeypatch
) -> None:
    """``limit=1000`` gets clamped to 25 so the LLM can't overstuff context."""
    ctx = seed_tool_workspace

    captured: dict[str, object] = {}

    from backend.app.services import knowledge_search as service_mod
    real_search = service_mod.search_workspace_knowledge

    async def _capturing_search(session, **kwargs):  # type: ignore[override]
        captured["limit"] = kwargs.get("limit")
        return await real_search(session, **kwargs)

    monkeypatch.setattr(
        "backend.app.services.knowledge_search.search_workspace_knowledge",
        _capturing_search,
    )
    _patch_embedder(monkeypatch, _FakeEmbedder({"auth": 0}))

    box = _make_toolbox(db_session, workspace_id=ctx["workspace"].id)
    raw = await box.invoke(
        "search_workspace_kb", {"query": "auth", "limit": 1000}
    )
    out = json.loads(raw)
    assert captured["limit"] == 25
    # Only 3 seeded articles; the cap just prevents over-reach.
    assert 0 < len(out["hits"]) <= 25


@pytest.mark.asyncio
async def test_search_workspace_kb_truncates_snippet(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Snippet stays ≤ 400 chars even when the article body is huge.

    Protects the chat window from a single oversize bucket eating
    thousands of tokens when the service's ``_first_paragraph``
    already truncates, and from any future regression where the
    service bumps its own cap above 400.
    """
    from backend.app.db.models.agent_memory import (
        BucketArticle,
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )

    _, _raw, workspace = seed_workspace
    bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="big",
        name="Big notes",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    body = "alpha " * 400
    db_session.add(
        BucketArticle(
            bucket_id=bucket.id,
            slug="main",
            title="A" * 500,
            body_md=body,
            content_sha="b" * 64,
            embedding=_unit_vec(3),
        )
    )
    await db_session.flush()

    _patch_embedder(monkeypatch, _FakeEmbedder({"big": 3}))
    box = _make_toolbox(db_session, workspace_id=workspace.id)
    raw = await box.invoke("search_workspace_kb", {"query": "big"})
    out = json.loads(raw)
    assert out["hits"], out
    hit = out["hits"][0]
    assert len(hit["snippet"]) <= 400
    # ``_truncate`` appends an ellipsis when it cuts, so 200 chars of
    # content plus the single-char marker is the tight bound.
    assert len(hit["title"]) <= 201
