"""Improvements surface (C8) — API contract.

Covers:

- Admin create, list, filter by decision.
- PATCH accept / decline / defer / reset with required-reason rule.
- Pipeline-authored ingress via ``run_token``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=913_001,
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
        external_id=93_013_013,
        full_name="acme/improvements-repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/improvements-repo",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_create_and_list(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/improvements",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "kind": "refactor",
            "title": "Extract payment service",
            "body": "Order service touches payments too often; extract.",
            "impact": "medium",
            "effort": "low",
            "context": {"files": ["orders.py"]},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["decision"] == "pending"
    assert body["kind"] == "refactor"

    listed = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/improvements",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_accept_and_filter(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/improvements",
            headers={"Authorization": f"Bearer {raw}"},
            json={
                "kind": "doc",
                "title": "Add onboarding README",
                "body": "Missing onboarding docs in api-backend repo.",
            },
        )
    ).json()

    accept = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/improvements/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "decision": "accepted",
            "next_action_url": "https://github.com/acme/widgets/pull/42",
        },
    )
    assert accept.status_code == 200
    assert accept.json()["decision"] == "accepted"
    assert accept.json()["decided_by_email"] is not None

    only_accepted = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/improvements?decision=accepted",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    assert len(only_accepted) == 1
    assert only_accepted[0]["next_action_url"].endswith("/pull/42")

    only_pending = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/improvements?decision=pending",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    assert len(only_pending) == 0


@pytest.mark.asyncio
async def test_decline_requires_reason(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/improvements",
            headers={"Authorization": f"Bearer {raw}"},
            json={"kind": "test", "title": "Cover pipelines", "body": "Add tests."},
        )
    ).json()

    bare = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/improvements/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "declined"},
    )
    assert bare.status_code == 422

    withreason = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/improvements/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "declined", "decision_reason": "coverage is acceptable"},
    )
    assert withreason.status_code == 200
    assert withreason.json()["decision_reason"] == "coverage is acceptable"


@pytest.mark.asyncio
async def test_reset_to_pending(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/improvements",
            headers={"Authorization": f"Bearer {raw}"},
            json={"kind": "arch", "title": "x", "body": "y"},
        )
    ).json()
    await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/improvements/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "accepted"},
    )
    reset = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/improvements/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "pending"},
    )
    assert reset.status_code == 200
    body = reset.json()
    assert body["decision"] == "pending"
    assert body["decided_at"] is None
    assert body["decided_by_email"] is None


@pytest.mark.asyncio
async def test_pipeline_ingress_creates_row(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    from backend.app.api.v1.routes import runs as runs_route
    from backend.app.db.models.agent_surface import Improvement
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.tenancy import AuditLog

    raw_pat, workspace, _install, repo = seed_repo_and_install

    async def _probe(*_a, **_kw):
        return frozenset({"parallel-audit-lanes.yml"})

    async def _dispatch(*_a, **_kw):
        return None

    monkeypatch.setattr(runs_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(runs_route, "dispatch_workflow", _dispatch)

    routine = Routine(
        workspace_id=workspace.id,
        repo_id=repo.id,
        lane_id="tech_debt",
        kind="event",
        pattern="parallel-audit-lanes",
    )
    db_session.add(routine)
    await db_session.flush()

    dispatch = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/routines/{routine.id}/runs",
        headers={"Authorization": f"Bearer {raw_pat}"},
    )
    assert dispatch.status_code == 202, dispatch.text
    run_json = dispatch.json()

    from backend.app.api.v1.routes.runs import (
        _hash_run_token,
        _mint_run_token,
    )
    from backend.app.core.config import get_settings

    settings = get_settings()
    raw_token = _mint_run_token(uuid.UUID(run_json["id"]), settings)
    run = await db_session.get(RoutineRun, uuid.UUID(run_json["id"]))
    run.run_token_hash = _hash_run_token(raw_token)
    await db_session.flush()
    workspace_id = workspace.id

    ingress = await v1_client.post(
        "/v1/improvements/pipeline",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "kind": "refactor",
            "title": "Extract /billing module",
            "body": "Billing code is intertwined with invoicing.",
            "impact": "high",
            "effort": "medium",
        },
    )
    assert ingress.status_code == 201, ingress.text

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Improvement).where(Improvement.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].impact == "high"

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "improvement.create.pipeline",
            )
        )
    ).scalars().all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_create_improvement_mirrors_to_inbox(
    v1_client, db_session, seed_workspace
) -> None:
    """P2-08 sanity: admin POST yields one legacy + one mirror row."""
    from backend.app.db.models.agent_surface import Improvement
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/improvements",
        headers={"Authorization": f"Bearer {raw}"},
        json={"kind": "doc", "title": "mirror sanity", "body": "body"},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    legacy = await db_session.get(Improvement, legacy_id)
    assert legacy is not None
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "improvements",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalars().all()
    assert len(mirror) == 1
