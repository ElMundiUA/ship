"""HTTP tests for ``/v1/workspaces/{ws}/fleet/requests`` (RFC-0008 §D).

Fleet Requests fan one catalog pattern out across many repos. The
tests here pin:

- happy-path fan-out: N valid repos → N dispatch_workflow calls,
  parent status ``dispatched``;
- best-effort rejection: one repo has no GitHub App, another is
  unknown to the workspace → parent lands as ``partial`` with both
  rejections surfaced in the POST response (and persisted for GET);
- validation failures (missing required inputs, empty repo_ids)
  don't create anything;
- GET detail rehydrates the same rejection list after a refresh;
- cancel flips parent + live children into ``cancel_requested``
  without touching terminal states.

GitHub ``workflow_dispatch`` is monkeypatched so nothing leaves the
process.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_fleet_repos(db_session, seed_workspace):
    """Two activated repos + a third unconfigured one.

    ``repo_a`` and ``repo_b`` share a live installation — both
    dispatch cleanly. ``repo_dead`` has ``installation_id=None`` so
    the pre-flight check rejects it with ``github_app_missing``
    without touching GitHub.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=9_999_001,
        account_login="acme",
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
        external_id=1001,
        full_name="acme/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/api",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=1002,
        full_name="acme/worker",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/worker",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    repo_dead = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=None,  # no install → pre-flight rejection
        provider="github",
        external_id=1003,
        full_name="acme/legacy",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/legacy",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add_all([repo_a, repo_b, repo_dead])
    await db_session.flush()
    return raw, workspace, install, (repo_a, repo_b, repo_dead)


@pytest.mark.asyncio
async def test_fleet_fan_out_happy_path(
    monkeypatch, v1_client, db_session, seed_fleet_repos
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, (repo_a, repo_b, _dead) = seed_fleet_repos

    captured: list[dict] = []

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        captured.append({"repo_id": repo.id, "inputs": dict(inputs)})

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {
                "issue_url": "https://linear.app/acme/issue/ACM-42",
            },
            "repo_ids": [str(repo_a.id), str(repo_b.id)],
            "title": "BA audit — Q2",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    parent = body["fleet_request"]
    assert parent["status"] == "dispatched"
    assert parent["pattern_id"] == "role-ba"
    assert parent["target_count"] == 2
    assert parent["dispatched_count"] == 2
    assert parent["rejected_count"] == 0
    assert parent["title"] == "BA audit — Q2"
    assert len(body["children"]) == 2
    assert body["rejections"] == []

    # Both dispatch_workflow calls saw the same pattern inputs.
    assert len(captured) == 2
    dispatched_repo_ids = {c["repo_id"] for c in captured}
    assert dispatched_repo_ids == {repo_a.id, repo_b.id}
    for call in captured:
        assert call["inputs"]["pattern_id"] == "role-ba"


@pytest.mark.asyncio
async def test_fleet_fan_out_best_effort_with_rejections(
    monkeypatch, v1_client, seed_fleet_repos
) -> None:
    """One live repo + one missing-install + one unknown id."""
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, (repo_a, _b, repo_dead) = seed_fleet_repos

    dispatch_calls: list[str] = []

    async def _dispatch(repo, install, workflow_file, *, inputs, settings, **_):
        dispatch_calls.append(str(repo.id))

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    ghost_id = uuid.uuid4()

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {"issue_url": "https://linear.app/acme/issue/ACM-1"},
            "repo_ids": [
                str(repo_a.id),
                str(repo_dead.id),
                str(ghost_id),
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    parent = body["fleet_request"]
    assert parent["status"] == "partial"
    assert parent["target_count"] == 3
    assert parent["dispatched_count"] == 1
    assert parent["rejected_count"] == 2

    # Only the live repo actually dispatched.
    assert dispatch_calls == [str(repo_a.id)]

    rejections = body["rejections"]
    codes = {r["code"] for r in rejections}
    assert codes == {"github_app_missing", "repo_not_found"}

    # GET detail rehydrates the same rejection list + child.
    detail = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/fleet/requests/{parent['id']}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["fleet_request"]["status"] == "partial"
    assert detail_body["fleet_request"]["rejected_count"] == 2
    detail_codes = {r["code"] for r in detail_body["rejections"]}
    assert detail_codes == {"github_app_missing", "repo_not_found"}
    assert len(detail_body["children"]) == 1


@pytest.mark.asyncio
async def test_fleet_fan_out_rejects_empty_repo_ids(
    monkeypatch, v1_client, seed_fleet_repos
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, *_ = seed_fleet_repos

    async def _dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {"issue_url": "https://linear.app/x"},
            "repo_ids": [],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "missing_repo_ids"


@pytest.mark.asyncio
async def test_fleet_fan_out_rejects_missing_inputs_before_any_dispatch(
    monkeypatch, v1_client, seed_fleet_repos
) -> None:
    """Pattern validation failures blow the whole fan-out up-front."""
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, (repo_a, repo_b, _dead) = seed_fleet_repos

    async def _dispatch(*args, **kwargs):
        raise AssertionError("dispatch must not run")

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {},
            "repo_ids": [str(repo_a.id), str(repo_b.id)],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "missing_required_inputs"


@pytest.mark.asyncio
async def test_fleet_cancel_flips_parent_and_live_children(
    monkeypatch, v1_client, seed_fleet_repos
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, (repo_a, repo_b, _dead) = seed_fleet_repos

    async def _dispatch(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    create_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "role-ba",
            "inputs": {"issue_url": "https://linear.app/x"},
            "repo_ids": [str(repo_a.id), str(repo_b.id)],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    parent_id = create_resp.json()["fleet_request"]["id"]

    cancel_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests/{parent_id}/cancel",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    cancel_body = cancel_resp.json()
    assert cancel_body["fleet_request"]["status"] == "cancel_requested"
    assert all(
        c["status"] == "cancel_requested" for c in cancel_body["children"]
    )

    # Cancel is idempotent.
    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/fleet/requests/{parent_id}/cancel",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 200
    assert second.json()["fleet_request"]["status"] == "cancel_requested"


@pytest.mark.asyncio
async def test_fleet_list_returns_newest_first(
    monkeypatch, v1_client, seed_fleet_repos
) -> None:
    from backend.app.integrations.github import workflows as gh_workflows

    raw, workspace, _install, (repo_a, repo_b, _dead) = seed_fleet_repos

    async def _dispatch(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gh_workflows, "dispatch_workflow", _dispatch)

    async def _create(title: str) -> str:
        resp = await v1_client.post(
            f"/v1/workspaces/{workspace.id}/fleet/requests",
            headers={"Authorization": f"Bearer {raw}"},
            json={
                "pattern_id": "role-ba",
                "inputs": {"issue_url": "https://linear.app/x"},
                "repo_ids": [str(repo_a.id)],
                "title": title,
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["fleet_request"]["id"]

    first_id = await _create("first")
    second_id = await _create("second")

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/fleet/requests",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()["requests"]
    assert [item["id"] for item in items[:2]] == [second_id, first_id]
