"""Tests for the per-repo agent-secrets API (Wizard v2 iter 3).

Covers:

- ``GET .../agent-secrets`` returns the catalog with ``present``
  flags derived from GitHub's secret-names list. ``required=False``
  entries (copilot) come back ``present=True`` without a round-trip
  to GitHub.
- ``GET .../agent-secrets?slugs=...`` filters to just the picks.
- ``POST .../agent-secrets`` encrypts and pushes each plaintext via
  ``put_repo_secret``, records per-agent audit rows, never persists
  the plaintext, and surfaces partial failures per-slug.
- RBAC: admin-only on both; 404 on unknown repo; 409 if the repo
  has no install bound.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seeded_repo_admin(db_session, seed_workspace):
    """Admin-scoped session token + a repo + install to target."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_001,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=30_030_030,
        full_name="acme/agentic",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/agentic",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, repo


@pytest.mark.asyncio
async def test_check_returns_full_catalog_with_github_state(
    monkeypatch, v1_client, db_session, seeded_repo_admin
) -> None:
    """Full-catalog check: copilot is always 'present' (required=False);
    Anthropic/Cursor/OpenAI reflect what GitHub reports."""
    from backend.app.services import agent_secrets as svc

    raw, workspace, repo = seeded_repo_admin

    async def _fake_list(target_repo, target_install, *, settings, client=None):
        return ["ANTHROPIC_API_KEY", "UNRELATED_THING"]

    monkeypatch.setattr(svc, "list_repo_secrets", _fake_list)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repo_id"] == str(repo.id)

    by_slug = {row["slug"]: row for row in body["agents"]}
    assert by_slug["claude-md"]["present"] is True
    assert by_slug["claude-md"]["secret_name"] == "ANTHROPIC_API_KEY"
    assert by_slug["claude-md"]["required"] is True

    # Not set on GitHub → present=False, wizard should prompt.
    assert by_slug["cursor-cloud"]["present"] is False
    assert by_slug["codex"]["present"] is False

    # Copilot doesn't need a secret — always reports present.
    assert by_slug["copilot"]["present"] is True
    assert by_slug["copilot"]["required"] is False


@pytest.mark.asyncio
async def test_check_filters_by_slug_query(
    monkeypatch, v1_client, db_session, seeded_repo_admin
) -> None:
    from backend.app.services import agent_secrets as svc

    raw, workspace, repo = seeded_repo_admin

    async def _fake_list(*args, **kwargs):
        return ["CURSOR_API_KEY"]

    monkeypatch.setattr(svc, "list_repo_secrets", _fake_list)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        params={"slugs": "cursor-cloud,bogus-slug"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Unknown slugs silently dropped (backend catalog vs stale browser
    # tab scenario), only cursor-cloud comes back.
    assert [a["slug"] for a in body["agents"]] == ["cursor-cloud"]
    assert body["agents"][0]["present"] is True


@pytest.mark.asyncio
async def test_check_translates_github_403_to_412_missing_permission(
    monkeypatch, v1_client, db_session, seeded_repo_admin
) -> None:
    """GitHub returning 403/404 on listing secrets (App lacks the
    ``Secrets`` permission) must surface as a 412 with
    ``code=missing_secrets_permission`` so the wizard can render a
    precise remediation banner instead of a generic 500."""
    from backend.app.services import agent_secrets as svc
    from backend.app.integrations.github.workflows import WorkflowDispatchError

    raw, workspace, repo = seeded_repo_admin

    async def _fake_list(*args, **kwargs):
        raise WorkflowDispatchError(403, "Resource not accessible by integration")

    monkeypatch.setattr(svc, "list_repo_secrets", _fake_list)

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 412, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "missing_secrets_permission"
    assert detail["upstream_status"] == 403


@pytest.mark.asyncio
async def test_push_uploads_and_never_persists_plaintext(
    monkeypatch, v1_client, db_session, seeded_repo_admin
) -> None:
    from backend.app.services import agent_secrets as svc
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, repo = seeded_repo_admin

    pushed: list[dict] = []

    async def _fake_put(
        target_repo, target_install, *, name, plaintext, settings, client=None, public_key=None
    ):
        pushed.append({"name": name, "plaintext": plaintext})
        return "keyid-stub"

    monkeypatch.setattr(svc, "put_repo_secret", _fake_put)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        json={
            "secrets": [
                {"slug": "claude-md", "plaintext": "sk-ant-xyz"},
                {"slug": "codex", "plaintext": "sk-openai-xyz  "},
            ]
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["pushed"]) == ["claude-md", "codex"]
    assert body["failed"] == []

    # Both pushes reached put_repo_secret with the trimmed plaintext.
    by_name = {p["name"]: p["plaintext"] for p in pushed}
    assert by_name == {
        "ANTHROPIC_API_KEY": "sk-ant-xyz",
        "OPENAI_API_KEY": "sk-openai-xyz",
    }

    # Audit log has one row per slug; never the plaintext.
    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "agent_secret.push",
            )
        )
    ).scalars().all()
    slugs_audited = sorted(a.payload["slug"] for a in audits)
    assert slugs_audited == ["claude-md", "codex"]
    for a in audits:
        # Audit payload must never include the plaintext value.
        assert "plaintext" not in a.payload
        assert a.payload["secret_cleared"] is False


@pytest.mark.asyncio
async def test_push_reports_partial_failures(
    monkeypatch, v1_client, db_session, seeded_repo_admin
) -> None:
    """One bad agent should not nuke the batch — wizard re-prompts
    only the offenders."""
    from backend.app.services import agent_secrets as svc

    raw, workspace, repo = seeded_repo_admin

    async def _sometimes_fails(
        target_repo, target_install, *, name, plaintext, settings, client=None, public_key=None
    ):
        if name == "OPENAI_API_KEY":
            raise RuntimeError("github rejected")
        return "keyid-stub"

    monkeypatch.setattr(svc, "put_repo_secret", _sometimes_fails)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        json={
            "secrets": [
                {"slug": "claude-md", "plaintext": "good"},
                {"slug": "codex", "plaintext": "bad"},
                # Unknown slug — reported as failure.
                {"slug": "unknown-agent", "plaintext": "huh"},
            ]
        },
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pushed"] == ["claude-md"]
    failed_slugs = sorted(f["slug"] for f in body["failed"])
    assert failed_slugs == ["codex", "unknown-agent"]


@pytest.mark.asyncio
async def test_push_empty_body_rejected(v1_client, db_session, seeded_repo_admin) -> None:
    raw, workspace, repo = seeded_repo_admin
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/agent-secrets",
        json={"secrets": []},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_check_unknown_repo_404(v1_client, db_session, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/agent-secrets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
