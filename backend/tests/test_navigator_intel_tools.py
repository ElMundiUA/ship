"""Navigator Phase-6 intel tools (Wave A read + Wave B mutate).

- ``repo_intel_get`` — returns the current snapshot when present;
  ``not_harvested_yet`` when absent.
- ``intel_harvest_trigger`` — admin-gated; mocks ``enqueue_harvest``;
  enforces a 1/hour rate-limit by reading our own audit rows.
- ``knowledge_search_v2`` — ``intel_facts=true`` prepends a
  synthetic intel summary hit; ``bucket_slug`` narrows results;
  explicit ``repo_id`` overrides the chat's active repo.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


EMBED_DIM = 1536


def _unit_vec(index: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[index % EMBED_DIM] = 1.0
    return v


class _FakeEmbedder:
    def __init__(self, mapping: dict[str, int]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, query: str, *, settings=None) -> list[float]:
        self.calls.append(query)
        idx = self._mapping.get(query.strip())
        if idx is None:
            return _unit_vec(999)
        return _unit_vec(idx)


def _patch_embedder(monkeypatch, embedder: _FakeEmbedder) -> None:
    import backend.app.services.knowledge_search as mod

    monkeypatch.setattr(mod, "embed_text", embedder)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toolbox(session, *, workspace_id, user_id, active_repo_id=None):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
        active_repo_id=active_repo_id,
    )


async def _make_user(db_session, *, email: str | None = None):
    from backend.app.db.models.tenancy import User

    u = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="A",
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _make_member(db_session, *, workspace_id, user_id, role="member"):
    from backend.app.db.models.tenancy import WorkspaceMember

    db_session.add(
        WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    )
    await db_session.flush()


async def _seed_repo(db_session, workspace, *, external_id: int, full_name: str):
    from backend.app.db.models.integrations import WorkspaceRepo

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=None,
        provider="github",
        external_id=external_id,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def _seed_intel(db_session, *, workspace_id, repo_id):
    from backend.app.db.models.repo_intel import RepoIntel

    intel = RepoIntel(
        workspace_id=workspace_id,
        repo_id=repo_id,
        version=1,
        is_current=True,
        languages={"python": 0.7, "typescript": 0.3},
        frameworks=["fastapi", "next.js"],
        package_managers=["pip", "npm"],
        entry_points=[{"path": "backend/app/main.py", "kind": "service"}],
        structure={"top_level_dirs": ["backend", "console"]},
        commit_style={"convention": "conventional"},
        visual_tokens={},
    )
    db_session.add(intel)
    await db_session.flush()
    return intel


# ---------------------------------------------------------------------------
# repo_intel_get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_intel_get_returns_current_snapshot(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=400_001, full_name="acme/intel-a"
    )
    await _seed_intel(db_session, workspace_id=ws.id, repo_id=repo.id)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("repo_intel_get", {"repo_id": str(repo.id)})
    )
    assert out["repo_id"] == str(repo.id)
    assert out["version"] == 1
    assert out["frameworks"] == ["fastapi", "next.js"]
    assert "harvested_at" in out


@pytest.mark.asyncio
async def test_repo_intel_get_not_harvested_yet(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=400_002, full_name="acme/intel-b"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("repo_intel_get", {"repo_id": str(repo.id)})
    )
    assert out["error"] == "not_harvested_yet"
    assert out["repo_id"] == str(repo.id)


@pytest.mark.asyncio
async def test_repo_intel_get_repo_not_in_workspace(
    db_session, seed_workspace
) -> None:
    """Cross-workspace tenancy guard via ``_verify_repo_in_workspace``."""
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-{uuid.uuid4().hex[:6]}", name="Other"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()
    foreign_repo = await _seed_repo(
        db_session, ws_b, external_id=400_003, full_name="acme/foreign"
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "repo_intel_get", {"repo_id": str(foreign_repo.id)}
        )
    )
    assert out["error"] == "repo_not_in_workspace"


# ---------------------------------------------------------------------------
# intel_harvest_trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_harvest_trigger_happy_path_enqueues(
    db_session, seed_workspace, monkeypatch
) -> None:
    """``enqueue_harvest`` is mocked; the tool returns ``status='queued'``."""
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=410_001, full_name="acme/intel-trigger"
    )

    calls: list[tuple] = []

    async def _fake_enqueue(redis_pool, workspace_id, repo_id, *, triggered_by):
        calls.append((workspace_id, repo_id, triggered_by))

    import backend.app.services.repo_intel as repo_intel_mod

    monkeypatch.setattr(repo_intel_mod, "enqueue_harvest", _fake_enqueue)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "intel_harvest_trigger", {"repo_id": str(repo.id)}
        )
    )
    assert out["status"] == "queued"
    assert out["repo_id"] == str(repo.id)
    assert calls == [(ws.id, repo.id, "manual_refresh")]


@pytest.mark.asyncio
async def test_intel_harvest_trigger_rate_limited(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Recent audit row → ``rate_limited`` with a positive retry hint."""
    from backend.app.db.models.tenancy import AuditLog

    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session, ws, external_id=410_002, full_name="acme/intel-rl"
    )

    # Pre-seed an audit row so the rate-limit guard fires immediately.
    db_session.add(
        AuditLog(
            workspace_id=ws.id,
            actor_user_id=user.id,
            action="navigator.tool.intel_harvest_trigger",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={"actor_kind": "navigator"},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
    )
    await db_session.flush()

    async def _should_not_call(*args, **kwargs):
        raise AssertionError("enqueue_harvest must NOT be invoked when rate-limited")

    import backend.app.services.repo_intel as repo_intel_mod

    monkeypatch.setattr(repo_intel_mod, "enqueue_harvest", _should_not_call)

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "intel_harvest_trigger", {"repo_id": str(repo.id)}
        )
    )
    assert out["error"] == "rate_limited"
    assert isinstance(out["retry_after_seconds"], int)
    assert out["retry_after_seconds"] > 0


@pytest.mark.asyncio
async def test_intel_harvest_trigger_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    repo = await _seed_repo(
        db_session, ws, external_id=410_003, full_name="acme/intel-fb"
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "intel_harvest_trigger", {"repo_id": str(repo.id)}
        )
    )
    assert out["error"] == "forbidden"


# ---------------------------------------------------------------------------
# knowledge_search_v2
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def kb_world(db_session, seed_workspace):
    """Two repos + per-repo + workspace buckets for v2 ranking tests."""
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

    user, _, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_900_001,
        account_login="kb",
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
        external_id=70_001,
        full_name="kb/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/kb/api",
        activated_at=datetime.now(timezone.utc),
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=70_002,
        full_name="kb/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/kb/web",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    a_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="auth-runbook",
        name="Auth runbook (api)",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_a.id,
    )
    b_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="release-notes",
        name="Release notes (web)",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_b.id,
    )
    db_session.add_all([a_bucket, b_bucket])
    await db_session.flush()

    db_session.add(
        BucketArticle(
            bucket_id=a_bucket.id,
            slug="main",
            title="Auth runbook (api)",
            body_md="Repo-A specific auth guidance for kb/api.",
            content_sha="ra" + "0" * 62,
            embedding=_unit_vec(0),
        )
    )
    db_session.add(
        BucketArticle(
            bucket_id=b_bucket.id,
            slug="main",
            title="Release notes (web)",
            body_md="Repo-B release notes for kb/web.",
            content_sha="rb" + "0" * 62,
            embedding=_unit_vec(1),
        )
    )
    await db_session.flush()

    return {
        "user": user,
        "workspace": workspace,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "a_bucket": a_bucket,
        "b_bucket": b_bucket,
    }


@pytest.mark.asyncio
async def test_knowledge_search_v2_intel_facts_prepends_intel_hit(
    db_session, kb_world, monkeypatch
) -> None:
    """``intel_facts=true`` injects a synthetic intel hit at index 0."""
    _patch_embedder(monkeypatch, _FakeEmbedder({"auth": 0}))
    await _seed_intel(
        db_session,
        workspace_id=kb_world["workspace"].id,
        repo_id=kb_world["repo_a"].id,
    )

    box = _toolbox(
        db_session,
        workspace_id=kb_world["workspace"].id,
        user_id=kb_world["user"].id,
        active_repo_id=kb_world["repo_a"].id,
    )
    out = json.loads(
        await box.invoke(
            "knowledge_search_v2",
            {"query": "auth", "intel_facts": True},
        )
    )
    assert out["results"], out
    head = out["results"][0]
    assert head["source"] == "repo_intel"
    assert head["rank_bucket"] == "intel"
    assert head["repo_id"] == str(kb_world["repo_a"].id)
    # Subsequent hits remain the standard knowledge entries.
    other_sources = {h["source"] for h in out["results"][1:]}
    assert "repo_intel" not in other_sources


@pytest.mark.asyncio
async def test_knowledge_search_v2_bucket_slug_narrows(
    db_session, kb_world, monkeypatch
) -> None:
    _patch_embedder(monkeypatch, _FakeEmbedder({"anything": 999}))
    box = _toolbox(
        db_session,
        workspace_id=kb_world["workspace"].id,
        user_id=kb_world["user"].id,
    )
    out = json.loads(
        await box.invoke(
            "knowledge_search_v2",
            {"query": "anything", "bucket_slug": "release-notes"},
        )
    )
    assert out["results"]
    for hit in out["results"]:
        assert hit["bucket_slug"] == "release-notes"


@pytest.mark.asyncio
async def test_knowledge_search_v2_explicit_repo_overrides_active(
    db_session, kb_world, monkeypatch
) -> None:
    """Explicit ``repo_id`` arg trumps the chat-context ``active_repo_id``."""
    _patch_embedder(monkeypatch, _FakeEmbedder({"auth": 1}))
    box = _toolbox(
        db_session,
        workspace_id=kb_world["workspace"].id,
        user_id=kb_world["user"].id,
        # Active repo points at A, but the arg below names B.
        active_repo_id=kb_world["repo_a"].id,
    )
    out = json.loads(
        await box.invoke(
            "knowledge_search_v2",
            {"query": "auth", "repo_id": str(kb_world["repo_b"].id)},
        )
    )
    assert out["results"]
    # The first hit should now correspond to repo_b — the explicit
    # arg won the override race.
    head = out["results"][0]
    assert head["rank_bucket"] == "repo_match"
    assert head["repo_id"] == str(kb_world["repo_b"].id)


@pytest.mark.asyncio
async def test_knowledge_search_v2_invalid_repo_id(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "knowledge_search_v2",
            {"query": "auth", "repo_id": "not-a-uuid"},
        )
    )
    assert out["error"] == "invalid_repo_id"
