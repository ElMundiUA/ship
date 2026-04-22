"""HTTP tests for workspace policies (RFC-0008 §G — PR-5).

Covers the mirror-lane MVP end-to-end: create the policy, list it
with a compliance rollup that correctly buckets activated repos into
``compliant`` / ``missing`` / ``excepted``, toggle exceptions, and
delete. Pattern validation (unknown pattern / request-only pattern)
is also pinned because the Console's policy form hits these paths
before ever touching the DB.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio


LANE_PATTERN_ID = "flow-daily-retro"
REQUEST_ONLY_PATTERN_ID = "flow-sprint-plan"


@pytest_asyncio.fixture
async def seed_policy_repos(db_session, seed_workspace):
    """Two activated repos + one wired Pipeline on the first."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline

    _, raw, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=7_000_001,
        account_login="policy",
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
        external_id=40001,
        full_name="pol/api",
        default_branch="main",
        private=False,
        html_url="https://github.com/pol/api",
        activated_at=datetime.now(timezone.utc),
        preset="api-backend",
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=40002,
        full_name="pol/web",
        default_branch="main",
        private=False,
        html_url="https://github.com/pol/web",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    # Wire the policy-matching lane on repo_a so the compliance
    # rollup sees it as already covered.
    db_session.add(
        Pipeline(
            workspace_id=workspace.id,
            repo_id=repo_a.id,
            name="Daily retro",
            workflow_id="scheduled-sdlc-lane",
            lane_id="daily_retro",
            enabled=True,
            config={},
        )
    )
    await db_session.flush()

    return raw, workspace, repo_a, repo_b


@pytest.mark.asyncio
async def test_create_and_list_policy_with_compliance(
    v1_client, seed_policy_repos
) -> None:
    raw, workspace, repo_a, repo_b = seed_policy_repos
    headers = {"Authorization": f"Bearer {raw}"}

    create_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "name": "Nightly retro",
            "pattern_id": LANE_PATTERN_ID,
            "lane_id": "daily_retro",
            "cadence": "@daily",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()

    # One compliant (repo_a has the lane wired), one missing (repo_b).
    assert created["pattern_id"] == LANE_PATTERN_ID
    assert created["compliance"]["total_repos"] == 2
    assert created["compliance"]["compliant"] == 1
    assert created["compliance"]["missing"] == 1
    assert created["compliance"]["excepted"] == 0

    statuses = {
        r["full_name"]: r["status"] for r in created["compliance"]["repos"]
    }
    assert statuses == {"pol/api": "compliant", "pol/web": "missing"}

    list_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    assert list_resp.status_code == 200, list_resp.text
    assert len(list_resp.json()["policies"]) == 1


@pytest.mark.asyncio
async def test_duplicate_lane_id_rejects(v1_client, seed_policy_repos) -> None:
    raw, workspace, _, _ = seed_policy_repos
    headers = {"Authorization": f"Bearer {raw}"}

    base = {
        "name": "First",
        "pattern_id": LANE_PATTERN_ID,
        "lane_id": "daily_retro",
        "cadence": "@daily",
    }
    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers, json=base
    )
    assert first.status_code == 201, first.text

    dup = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={**base, "name": "Second"},
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "policy_lane_conflict"


@pytest.mark.asyncio
async def test_pattern_must_advertise_lane_mode(
    v1_client, seed_policy_repos
) -> None:
    raw, workspace, _, _ = seed_policy_repos
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "name": "Bad",
            "pattern_id": REQUEST_ONLY_PATTERN_ID,
            "lane_id": "bad_lane",
            "cadence": "@daily",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "pattern_not_lane_mode"


@pytest.mark.asyncio
async def test_unknown_pattern_404(v1_client, seed_policy_repos) -> None:
    raw, workspace, _, _ = seed_policy_repos
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "name": "Ghost",
            "pattern_id": "definitely-not-a-real-pattern",
            "lane_id": "ghost",
            "cadence": "@daily",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_exception_toggle_moves_repo_bucket(
    v1_client, seed_policy_repos
) -> None:
    raw, workspace, _, repo_b = seed_policy_repos
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "name": "Nightly retro",
            "pattern_id": LANE_PATTERN_ID,
            "lane_id": "daily_retro",
            "cadence": "@daily",
        },
    )
    policy_id = create.json()["id"]

    add_exc = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}/exceptions/{repo_b.id}",
        headers=headers,
        json={"reason": "legacy repo — managed elsewhere"},
    )
    assert add_exc.status_code == 200, add_exc.text
    body = add_exc.json()
    assert body["compliance"]["missing"] == 0
    assert body["compliance"]["excepted"] == 1
    excepted_row = next(
        r for r in body["compliance"]["repos"] if r["repo_id"] == str(repo_b.id)
    )
    assert excepted_row["status"] == "excepted"
    assert excepted_row["exception_reason"] == "legacy repo — managed elsewhere"

    # Re-add is idempotent, updates reason in place.
    re_add = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}/exceptions/{repo_b.id}",
        headers=headers,
        json={"reason": "new reason"},
    )
    assert re_add.status_code == 200
    assert re_add.json()["compliance"]["excepted"] == 1

    remove = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}/exceptions/{repo_b.id}",
        headers=headers,
    )
    assert remove.status_code == 200, remove.text
    assert remove.json()["compliance"]["excepted"] == 0
    assert remove.json()["compliance"]["missing"] == 1


@pytest.mark.asyncio
async def test_delete_policy(v1_client, seed_policy_repos) -> None:
    raw, workspace, _, _ = seed_policy_repos
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "name": "Nightly retro",
            "pattern_id": LANE_PATTERN_ID,
            "lane_id": "daily_retro",
            "cadence": "@daily",
        },
    )
    policy_id = create.json()["id"]

    delete = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}", headers=headers
    )
    assert delete.status_code == 204

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    assert listing.json()["policies"] == []
