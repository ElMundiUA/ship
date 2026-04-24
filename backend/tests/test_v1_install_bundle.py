"""Multi-preset bundle install endpoint (A1).

Exercises ``POST /v1/workspaces/{ws}/repos/{repo}/install_bundle``:
one PR, every workflow YAML the preset(s) need, plus a ``.ship/``
config stub. The single-preset flow (``/pipelines/{id}/install``)
stays intact — this is the "Install everything" shortcut that makes
WOW onboarding a one-click reality.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_workspace_with_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=888_001,
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
        external_id=42_888_001,
        full_name="acme/bundle-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/bundle-target",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_install_bundle_opens_single_pr_for_persisted_preset(
    monkeypatch, v1_client, db_session, seed_workspace_with_repo
) -> None:
    """No body → bundle picks the repo's persisted preset and ships it."""
    from backend.app.api.v1.routes import repos as repos_route
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_workspace_with_repo

    captured: dict[str, object] = {}

    async def _commit(
        repo, install, *, files, title, branch_label, pr_body_header, settings,
        return_url=None, client=None,
    ):
        captured["files"] = [p for p, _ in files]
        captured["branch_label"] = branch_label
        captured["title"] = title
        captured["return_url"] = return_url
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/bundle-target/pull/99",
            pr_number=99,
            branch="ship/bundle-web-app-123",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr", _commit
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/install_bundle",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pr_number"] == 99
    assert body["pr_url"].endswith("/pull/99")
    # Post-P5-01 the persisted legacy preset (``"web-app"``) collapses
    # to ``"default"`` before bundle composition; that's what surfaces
    # in the response + the PR branch label.
    assert body["presets"] == ["default"]

    # ``"default"`` enables pr_review/daily_standup/tech_debt/code_map;
    # every enabled lane with a catalog YAML lands in the bundle
    # (code_map stays YAML-less), plus the .ship/config.yml stub.
    assert ".ship/config.yml" in body["files"]
    assert any(
        f.startswith(".github/workflows/") for f in body["files"]
    )
    assert captured["branch_label"] == "default"
    assert captured["return_url"] is not None


@pytest.mark.asyncio
async def test_install_bundle_collapses_multiple_legacy_presets_to_default(
    monkeypatch, v1_client, db_session, seed_workspace_with_repo
) -> None:
    """Multiple legacy presets all normalize to ``"default"`` and dedupe
    to a single entry — post-P5-01 the only meaningful preset is
    ``"default"`` so a multi-preset install bundle becomes a single
    canonical bundle."""
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_workspace_with_repo

    async def _commit(repo, install, *, files, **_):
        paths = [p for p, _ in files]
        # Path uniqueness is the core invariant the tree builder relies on.
        assert len(paths) == len(set(paths))
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/bundle-target/pull/100",
            pr_number=100,
            branch="ship/bundle-default-124",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr", _commit
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/install_bundle",
        headers={"Authorization": f"Bearer {raw}"},
        json={"presets": ["web-app", "api-backend"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Both legacy ids collapse + dedupe → exactly one entry.
    assert body["presets"] == ["default"]


@pytest.mark.asyncio
async def test_install_bundle_accepts_legacy_preset_no_422(
    monkeypatch, v1_client, seed_workspace_with_repo
) -> None:
    """The pre-P5-01 422-on-unknown gate is gone: legacy preset
    strings (and any catalog-authored future ids) are accepted at the
    boundary. Legacy ids normalize to ``"default"``, which yields the
    canonical bundle."""
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_workspace_with_repo

    async def _commit(repo, install, *, files, **_):
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/bundle-target/pull/101",
            pr_number=101,
            branch="ship/bundle-default-125",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr", _commit
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/install_bundle",
        headers={"Authorization": f"Bearer {raw}"},
        json={"presets": ["mobile-app-deep"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["presets"] == ["default"]


@pytest.mark.asyncio
async def test_install_bundle_412_when_no_preset_available(
    v1_client, db_session, seed_workspace
) -> None:
    """Repo without a preset + empty body → structured 412."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=888_002,
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
        external_id=42_888_002,
        full_name="acme/no-preset",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/no-preset",
        activated_at=datetime.now(timezone.utc),
        # preset=None — the legacy shape we're defending against.
    )
    db_session.add(repo)
    await db_session.flush()

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/install_bundle",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "preset_required"
