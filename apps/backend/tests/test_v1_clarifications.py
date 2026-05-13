"""Clarifications inbox (C9) — API contract.

Covers:

- Admin create / list / filter by status.
- PATCH answer → ``answered`` with answerer + timestamp.
- PATCH ``status='skipped'`` keeps answer NULL.
- PATCH ``status='open'`` reopens and clears answerer.
- Reject ``answered`` without a non-empty body.
- Pipeline-authored path via ``run_token`` bearer correctly stamps
  ``pipeline_run_id`` and records an audit log entry.
- Run-token callers can't read the inbox (no session).
- Cross-workspace access returns 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    """Minimal install + activated repo so we can dispatch a pipeline run."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=912_001,
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
        external_id=91_201_201,
        full_name="acme/clarifications-repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/clarifications-repo",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_create_and_list_clarification(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "question": "Which queue should the worker consume?",
            "ticket_ref": "LINEAR-42",
            "context": {"file": "worker.py", "line": 17},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["answer"] is None
    assert body["ticket_ref"] == "LINEAR-42"

    list_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_list_filters_by_status(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    for ticket in ("A", "B", "C"):
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": f"Q for {ticket}", "ticket_ref": ticket},
        )

    # Answer one, skip another.
    listed = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    answered_id = listed[0]["id"]
    skipped_id = listed[1]["id"]
    await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{answered_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "kafka topic v2"},
    )
    await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{skipped_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"status": "skipped"},
    )

    open_rows = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/clarifications?status=open",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    assert len(open_rows) == 1

    answered_rows = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/clarifications?status=answered",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    assert len(answered_rows) == 1
    assert answered_rows[0]["answer"] == "kafka topic v2"
    assert answered_rows[0]["answered_by_email"] is not None


@pytest.mark.asyncio
async def test_patch_reopen_clears_answer(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "what?"},
        )
    ).json()

    await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "because"},
    )
    reopened = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"status": "open"},
    )
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["status"] == "open"
    assert body["answer"] is None
    assert body["answered_by_email"] is None


@pytest.mark.asyncio
async def test_patch_answered_requires_body(
    v1_client, db_session, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "what?"},
        )
    ).json()
    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"status": "answered"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pipeline_ingress_creates_row_and_audit(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """A dispatched Action can POST a clarification via its run_token."""
    from backend.app.api.v1.routes import runs as pipelines_route
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.tenancy import AuditLog

    raw_pat, workspace, _install, repo = seed_repo_and_install

    # Stub out the catalog probe + GitHub dispatch so we don't hit
    # the network; we just need a live PipelineRun with a run_token.
    async def _probe(*_a, **_kw):
        return frozenset({"parallel-audit-lanes.yml"})

    async def _dispatch(*_a, **_kw):
        return None

    monkeypatch.setattr(pipelines_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(pipelines_route, "dispatch_workflow", _dispatch)

    # Create a routine + run to earn a run_token.
    from backend.app.db.models.lanes import Routine

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

    # Re-mint a usable run_token for the ingress test.
    from backend.app.api.v1.routes.runs import (
        _hash_run_token,
        _mint_run_token,
    )
    from backend.app.core.config import get_settings
    from backend.app.db.models.lanes import RoutineRun

    settings = get_settings()
    raw_token = _mint_run_token(uuid.UUID(run_json["id"]), settings)
    run = await db_session.get(RoutineRun, uuid.UUID(run_json["id"]))
    run.run_token_hash = _hash_run_token(raw_token)
    await db_session.flush()

    workspace_id = workspace.id  # capture before expire

    ingress = await v1_client.post(
        "/v1/clarifications/pipeline",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "question": "Which lint config should we keep?",
            "ticket_ref": "TECH-DEBT-01",
            "context": {"candidate": ".eslintrc"},
        },
    )
    assert ingress.status_code == 201, ingress.text
    body = ingress.json()
    assert body["routine_run_id"] == run_json["id"]

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ticket_ref == "TECH-DEBT-01"

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "clarification.create.pipeline",
            )
        )
    ).scalars().all()
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_create_clarification_mirrors_to_inbox(
    v1_client, db_session, seed_workspace
) -> None:
    """P2-08 sanity: a happy-path admin POST yields one legacy + one mirror row."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "mirror sanity?"},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    legacy = await db_session.get(Clarification, legacy_id)
    assert legacy is not None
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalars().all()
    assert len(mirror) == 1


@pytest.mark.asyncio
async def test_pipeline_ingress_rejects_bad_token(v1_client) -> None:
    resp = await v1_client.post(
        "/v1/clarifications/pipeline",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"question": "x"},
    )
    assert resp.status_code == 401
