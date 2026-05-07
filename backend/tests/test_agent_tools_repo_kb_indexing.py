"""Navigator ``repo_kb_status`` and ``reindex_repo_kb`` tools (ELS-62).

Two complementary surfaces over :mod:`backend.app.services.agent.kb_indexer`:

- ``repo_kb_status`` — read-only probe: returns chunk count, path
  count, and last ``indexed_at`` from ``kb_chunks`` for one repo.
- ``reindex_repo_kb`` — admin-gated trigger: synchronously runs the
  indexer and returns the :class:`IndexReport` shape so the operator
  sees concrete file / chunk counters in the chat transcript.

Coverage matches the well-trodden agent-tool checklist: happy path,
empty state, validation, cross-workspace tenancy, admin gating,
embeddings-unavailable. The trigger's GitHub round-trip is stubbed
out via a :func:`monkeypatch` of ``reindex_repo_kb`` itself so the
tests stay offline; the indexer's own behaviour is covered by
``test_agent_kb_indexer.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.models.agent_memory import KbChunk
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.services.agent.embedding import EMBED_DIM


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


def _zero_vec() -> list[float]:
    return [0.0] * EMBED_DIM


async def _seed_install(db_session, *, workspace_id, installation_id=8_700_001):
    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    return install


async def _seed_repo(
    db_session,
    *,
    workspace_id,
    install_id=None,
    external_id: int = 600_001,
    full_name: str = "acme/repo",
) -> WorkspaceRepo:
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install_id,
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


async def _seed_chunks(
    db_session,
    *,
    workspace_id,
    repo_id,
    rows: list[tuple[str, int]],
    indexed_at: datetime | None = None,
) -> None:
    for source_path, chunk_index in rows:
        db_session.add(
            KbChunk(
                workspace_id=workspace_id,
                repo_id=repo_id,
                source_path=source_path,
                chunk_index=chunk_index,
                content=f"chunk {source_path}#{chunk_index}",
                content_sha="0" * 64,
                embedding=_zero_vec(),
                **(
                    {"indexed_at": indexed_at}
                    if indexed_at is not None
                    else {}
                ),
            )
        )
    await db_session.flush()


# ---------------------------------------------------------------------------
# repo_kb_status — probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_kb_status_returns_zero_when_never_indexed(
    db_session, seed_workspace
) -> None:
    """No chunks rows → ``indexed=False`` with concrete zeros, not ``null``."""
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        external_id=600_001,
        full_name="acme/empty",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("repo_kb_status", {"repo_id": str(repo.id)})
    )

    assert out["repo_id"] == str(repo.id)
    assert out["repo_full_name"] == "acme/empty"
    assert out["default_branch"] == "main"
    assert out["kb_root"] == ".ship/knowledge"
    assert out["indexed"] is False
    assert out["chunks"] == 0
    assert out["paths"] == 0
    assert out["last_indexed_at"] is None


@pytest.mark.asyncio
async def test_repo_kb_status_aggregates_chunks_paths_and_timestamp(
    db_session, seed_workspace
) -> None:
    """Two paths × multiple chunks each — counts add up; max indexed_at wins."""
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        external_id=600_002,
        full_name="acme/full",
    )

    older = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(hours=2)
    await _seed_chunks(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        rows=[
            (".ship/knowledge/a.md", 0),
            (".ship/knowledge/a.md", 1),
            (".ship/knowledge/b.md", 0),
        ],
        indexed_at=older,
    )
    # Add one more chunk on a third path with a newer timestamp so the
    # MAX picks the latest one.
    await _seed_chunks(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        rows=[(".ship/knowledge/c.md", 0)],
        indexed_at=newer,
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("repo_kb_status", {"repo_id": str(repo.id)})
    )

    assert out["indexed"] is True
    assert out["chunks"] == 4
    assert out["paths"] == 3
    assert out["last_indexed_at"] == newer.isoformat()


@pytest.mark.asyncio
async def test_repo_kb_status_rejects_cross_workspace_repo(
    db_session, seed_workspace
) -> None:
    """A repo id from another workspace must not leak its state."""
    from backend.app.db.models.tenancy import Workspace

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    foreign_repo = await _seed_repo(
        db_session,
        workspace_id=ws_b.id,
        external_id=600_003,
        full_name="acme/foreign",
    )
    await _seed_chunks(
        db_session,
        workspace_id=ws_b.id,
        repo_id=foreign_repo.id,
        rows=[(".ship/knowledge/x.md", 0)],
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "repo_kb_status", {"repo_id": str(foreign_repo.id)}
        )
    )
    assert out["error"] == "repo_not_found"


@pytest.mark.asyncio
async def test_repo_kb_status_works_without_github_installation(
    db_session, seed_workspace
) -> None:
    """A suspended / removed install must not hide already-embedded state.

    Once the workspace has paid to embed knowledge, the agent should
    still be able to *see* what's there — even if the GitHub App was
    removed and a fresh re-index would currently fail. This is the
    deliberate split with ``reindex_repo_kb`` (which does require a
    live install).
    """
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        install_id=None,
        external_id=600_004,
        full_name="acme/no-install",
    )
    await _seed_chunks(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        rows=[(".ship/knowledge/runbook.md", 0)],
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("repo_kb_status", {"repo_id": str(repo.id)})
    )
    assert out["indexed"] is True
    assert out["chunks"] == 1


# ---------------------------------------------------------------------------
# reindex_repo_kb — trigger
# ---------------------------------------------------------------------------


def _stub_index_report(repo_id: uuid.UUID, **overrides):
    from backend.app.services.agent.kb_indexer import IndexReport

    fields = {
        "repo_id": str(repo_id),
        "files_discovered": 3,
        "files_indexed": 2,
        "files_skipped_unchanged": 1,
        "files_skipped_too_big": 0,
        "files_skipped_binary": 0,
        "chunks_deleted": 4,
        "chunks_written": 7,
    }
    fields.update(overrides)
    return IndexReport(**fields)


@pytest.mark.asyncio
async def test_reindex_repo_kb_returns_index_report(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, ws = seed_workspace
    install = await _seed_install(db_session, workspace_id=ws.id)
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        install_id=install.id,
        external_id=600_010,
        full_name="acme/reindex-target",
    )

    captured: dict[str, object] = {}

    async def _fake_reindex(session, repo_arg, install_arg, *, settings=None):
        captured["repo_id"] = repo_arg.id
        captured["install_id"] = install_arg.id
        return _stub_index_report(repo_arg.id)

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb",
        _fake_reindex,
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("reindex_repo_kb", {"repo_id": str(repo.id)})
    )

    assert out["repo_id"] == str(repo.id)
    assert out["repo_full_name"] == "acme/reindex-target"
    assert out["files_discovered"] == 3
    assert out["files_indexed"] == 2
    assert out["files_skipped_unchanged"] == 1
    assert out["files_skipped_too_big"] == 0
    assert out["files_skipped_binary"] == 0
    assert out["chunks_written"] == 7
    assert out["chunks_deleted"] == 4
    assert captured["repo_id"] == repo.id
    assert captured["install_id"] == install.id


@pytest.mark.asyncio
async def test_reindex_repo_kb_blocks_member_role(
    db_session, seed_workspace, monkeypatch
) -> None:
    """A member-role caller is rejected before the indexer is called."""
    from backend.app.db.models.tenancy import User, WorkspaceMember

    _, _, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_700_011
    )
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        install_id=install.id,
        external_id=600_011,
        full_name="acme/admin-only",
    )
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

    indexer_called = {"count": 0}

    async def _boom(*_args, **_kwargs):
        indexer_called["count"] += 1
        raise AssertionError("indexer should not run for non-admin")

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb", _boom
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke("reindex_repo_kb", {"repo_id": str(repo.id)})
    )
    assert out["error"] == "forbidden"
    assert indexer_called["count"] == 0


@pytest.mark.asyncio
async def test_reindex_repo_kb_rejects_repo_without_installation(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Repo that exists but has no GitHub App install → ``repo_unavailable``."""
    user, _, ws = seed_workspace
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        install_id=None,
        external_id=600_012,
        full_name="acme/orphan-repo",
    )

    async def _boom(*_args, **_kwargs):
        raise AssertionError("indexer should not run when install is missing")

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb", _boom
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("reindex_repo_kb", {"repo_id": str(repo.id)})
    )
    assert out["error"] == "repo_unavailable"
    assert "installation" in out["message"].lower()


@pytest.mark.asyncio
async def test_reindex_repo_kb_surfaces_embeddings_unavailable(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Indexer's RuntimeError (no OPENAI_API_KEY) becomes a structured error."""
    user, _, ws = seed_workspace
    install = await _seed_install(
        db_session, workspace_id=ws.id, installation_id=8_700_013
    )
    repo = await _seed_repo(
        db_session,
        workspace_id=ws.id,
        install_id=install.id,
        external_id=600_013,
        full_name="acme/no-embed",
    )

    async def _no_key(*_args, **_kwargs):
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb", _no_key
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("reindex_repo_kb", {"repo_id": str(repo.id)})
    )
    assert out["error"] == "embeddings_unavailable"
    assert "OPENAI_API_KEY" in out["message"]


@pytest.mark.asyncio
async def test_reindex_repo_kb_rejects_cross_workspace_repo(
    db_session, seed_workspace, monkeypatch
) -> None:
    """A repo id from another workspace surfaces ``repo_unavailable``."""
    from backend.app.db.models.tenancy import Workspace

    user, _, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-b-{uuid.uuid4().hex[:6]}", name="B"
    )
    db_session.add(ws_b)
    await db_session.flush()
    install_b = await _seed_install(
        db_session, workspace_id=ws_b.id, installation_id=8_700_014
    )
    foreign_repo = await _seed_repo(
        db_session,
        workspace_id=ws_b.id,
        install_id=install_b.id,
        external_id=600_014,
        full_name="acme/foreign",
    )

    async def _boom(*_args, **_kwargs):
        raise AssertionError("indexer should not run across tenants")

    monkeypatch.setattr(
        "backend.app.services.agent.kb_indexer.reindex_repo_kb", _boom
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "reindex_repo_kb", {"repo_id": str(foreign_repo.id)}
        )
    )
    assert out["error"] == "repo_unavailable"


# ---------------------------------------------------------------------------
# Spec / handler wiring — guard against future drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_tools_appear_in_specs_with_required_repo_id(
    db_session, seed_workspace
) -> None:
    """The LLM must see both tools and require ``repo_id`` on each."""
    user, _, ws = seed_workspace
    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    by_name = {spec.name: spec for spec in box.specs()}

    assert "repo_kb_status" in by_name
    assert "reindex_repo_kb" in by_name
    for name in ("repo_kb_status", "reindex_repo_kb"):
        params = by_name[name].parameters
        assert params["required"] == ["repo_id"], name
        assert "repo_id" in params["properties"], name
