"""HTTP tests for ``/v1/workspaces/{ws}/repos/{id}/requests`` (RFC-0008 C4).

Exercises the pattern-backed dispatch path introduced in C4 alongside
the legacy ``{agent_slug, prompt}`` shape kept for ad-hoc runs. The
GitHub ``workflow_dispatch`` call is monkeypatched so the tests don't
touch the network — we assert on the captured ``inputs`` dict to pin
the wire contract the ``adhoc-agent-run.yml`` workflow consumes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=999_404,
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
        external_id=42_999_404,
        full_name="acme/requests-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/requests-target",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_dispatch_request_with_pattern_id_validates_and_forwards_inputs(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, repo = seed_repo_and_install

    captured: dict[str, object] = {}

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured["workflow_file"] = workflow_file
        captured["inputs"] = dict(inputs)

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {
                "issue_url": "https://linear.app/acme/issue/ACM-42",
                "depth": "thorough",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["pattern_id"] == "role-ba"
    assert body["inputs"] == {
        "issue_url": "https://linear.app/acme/issue/ACM-42",
        "depth": "thorough",
    }
    assert body["status"] == "dispatched"

    assert captured["workflow_file"] == "adhoc-agent-run.yml"
    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["pattern_id"] == "role-ba"
    payload = json.loads(inputs["pattern_inputs_json"])
    assert payload == {
        "issue_url": "https://linear.app/acme/issue/ACM-42",
        "depth": "thorough",
    }


@pytest.mark.asyncio
async def test_dispatch_request_missing_required_input_422(
    monkeypatch, v1_client, seed_repo_and_install
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, repo = seed_repo_and_install

    async def _dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run when validation fails")

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={"pattern_id": "role-ba", "inputs": {}},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "missing_required_inputs"
    assert "issue_url" in detail["missing"]


@pytest.mark.asyncio
async def test_dispatch_request_rejects_unknown_pattern(
    monkeypatch, v1_client, seed_repo_and_install
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, repo = seed_repo_and_install

    async def _dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={"pattern_id": "does-not-exist", "inputs": {}},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "pattern_not_found"


@pytest.mark.asyncio
async def test_dispatch_request_rejects_pattern_without_request_mode(
    monkeypatch, v1_client, seed_repo_and_install
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, repo = seed_repo_and_install

    async def _dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    # ``common-base`` has ``modes: []`` by design — it's a shared
    # fragment, not a user-dispatchable pattern.
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={"pattern_id": "common-base", "inputs": {}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "pattern_not_request_mode"


@pytest.mark.asyncio
async def test_dispatch_request_legacy_adhoc_shape_still_works(
    monkeypatch, v1_client, seed_repo_and_install
) -> None:
    """Ad-hoc dispatches without ``pattern_id`` keep the old contract."""
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, repo = seed_repo_and_install

    captured: dict[str, object] = {}

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured["inputs"] = dict(inputs)

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "agent_slug": "claude",
            "prompt": "Audit the payments module.",
            "context_ref": "src/payments.py",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["pattern_id"] is None
    assert body["inputs"] == {}
    assert body["agent_slug"] == "claude"

    inputs = captured["inputs"]
    assert "pattern_id" not in inputs
    assert "pattern_inputs_json" not in inputs
    assert inputs["prompt"] == "Audit the payments module."


@pytest.mark.asyncio
async def test_list_catalog_patterns_filters_by_mode(
    v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/patterns?mode=request",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {entry["id"] for entry in body}
    assert "role-ba" in ids
    # common-* fragments never surface as picker entries.
    assert "common-base" not in ids
    for entry in body:
        # Every returned entry must advertise request-mode (modern
        # metadata) or be legacy (empty modes + no category).
        modes = entry["modes"]
        category = entry["category"]
        assert (not modes and category is None) or "request" in modes


@pytest.mark.asyncio
async def test_list_catalog_patterns_rejects_invalid_mode(
    v1_client, seed_workspace
) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/patterns?mode=bogus",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_mode"
