"""ELS-144 — inbox noise reduction on agent finish + stale sweep."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from backend.app.api.v1.routes import agent_runs as agent_runs_routes
from backend.app.db.models.inbox import InboxItem, InboxItemEvent
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.services.dispatcher import (
    ENV_SEPARATION_PENDING_KEY,
    _emit_env_separation_warning,
)
from backend.app.services.inbox.sweep import sweep_stale_inbox_items
from backend.app.services.tracker_resolver import ResolvedTracker


def _finish_payload(**overrides):
    base = {
        "run_id": f"run-{uuid.uuid4().hex[:8]}",
        "outcome": "ready_next_step",
        "fsm_stage": "dev_implementation",
        "stage_next": "qa_manual",
        "ticket_ref": "ELS-144",
        "comment": "Done. PR: https://github.com/o/r/pull/1 [Ship SDLC:role-developer]",
        "process": "development",
    }
    base.update(overrides)
    return base


class _FakeGateway:
    def __init__(self) -> None:
        self.transition = AsyncMock()

    async def comment(self, _ref, *, body: str) -> None:
        return None


@pytest.fixture
def fake_tracker(monkeypatch):
    gateway = _FakeGateway()
    resolved = ResolvedTracker(
        kind="memory",
        gateway=gateway,
        scope_hint=None,
        source="legacy",
    )

    async def _resolve(*_a, **_k):
        return resolved

    monkeypatch.setattr(
        agent_runs_routes,
        "resolve_for_workspace",
        _resolve,
    )
    return gateway


@pytest.mark.asyncio
async def test_blocked_finish_does_not_create_inbox_row(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            outcome="blocked",
            stage_next=None,
            comment="Push refused. [Ship SDLC:role-developer]",
        ),
    )
    assert res.status_code == 200, res.text
    assert "inbox:blocker" not in res.json().get("actions", [])

    inbox_count = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert inbox_count.status_code == 200
    assert inbox_count.json()["total"] == 0

    audit_count = await db_session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.workspace_id == ws.id,
            AuditLog.action == "agent_run.finish",
        )
    )
    assert int(audit_count or 0) >= 1


@pytest.mark.asyncio
async def test_ready_next_step_sweeps_auto_resolvable_rows(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    _, raw, ws = seed_workspace
    item = InboxItem(
        workspace_id=ws.id,
        type="stuck",
        status="new",
        title="stuck mirror",
        summary="s",
        payload={"ticket_ref": "ELS-144", "fsm_stage": "dev_implementation"},
        auto_resolvable=True,
    )
    db_session.add(item)
    await db_session.flush()

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200, res.text
    actions = res.json().get("actions", [])
    assert any(a.startswith("inbox:sweep_auto_resolved:") for a in actions)

    await db_session.refresh(item)
    assert item.status == "resolved"
    assert item.resolution == "auto_recovered"
    assert item.resolved_at is not None

    events = (
        await db_session.execute(
            select(InboxItemEvent).where(InboxItemEvent.item_id == item.id)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].actor_kind == "system"
    assert events[0].action == "auto_recovered"


@pytest.mark.asyncio
async def test_out_of_scope_sweeps_auto_resolvable_rows(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    _, raw, ws = seed_workspace
    item = InboxItem(
        workspace_id=ws.id,
        type="stuck",
        status="new",
        title="stuck mirror",
        summary="s",
        payload={"ticket_ref": "ELS-144", "fsm_stage": "dev_implementation"},
        auto_resolvable=True,
    )
    db_session.add(item)
    await db_session.flush()

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            outcome="out_of_scope",
            stage_next=None,
            comment="Not for this pipeline. [Ship SDLC:role-developer]",
        ),
    )
    assert res.status_code == 200, res.text
    await db_session.refresh(item)
    assert item.status == "resolved"
    assert item.resolution == "auto_recovered"


@pytest.mark.asyncio
async def test_snoozed_auto_resolvable_not_swept(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    _, raw, ws = seed_workspace
    item = InboxItem(
        workspace_id=ws.id,
        type="stuck",
        status="snoozed",
        title="snoozed",
        summary="s",
        payload={"ticket_ref": "ELS-144", "fsm_stage": "dev_implementation"},
        auto_resolvable=True,
        snoozed_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(item)
    await db_session.flush()

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 200
    await db_session.refresh(item)
    assert item.status == "snoozed"


@pytest.mark.asyncio
async def test_non_auto_resolvable_not_swept(
    db_session, v1_client, seed_workspace, fake_tracker
) -> None:
    _, raw, ws = seed_workspace
    item = InboxItem(
        workspace_id=ws.id,
        type="blocker",
        status="new",
        title="real blocker",
        summary="s",
        payload={"ticket_ref": "ELS-144", "fsm_stage": "dev_implementation"},
        auto_resolvable=False,
    )
    db_session.add(item)
    await db_session.flush()

    await v1_client.post(
        f"/v1/workspaces/{ws.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    await db_session.refresh(item)
    assert item.status == "new"


@pytest.mark.asyncio
async def test_stale_sweep_cron_dismisses_old_rows(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    item = InboxItem(
        workspace_id=ws.id,
        type="stuck",
        status="new",
        title="stale row",
        summary="s",
        payload={},
        auto_resolvable=True,
        stale_after=timedelta(hours=1),
        created_at=old,
    )
    db_session.add(item)
    await db_session.flush()

    swept = await sweep_stale_inbox_items(db_session)
    assert swept == 1
    await db_session.refresh(item)
    assert item.status == "dismissed"
    assert item.resolution == "stale"


@pytest.mark.asyncio
async def test_env_separation_warning_no_inbox_row(
    db_session, seed_workspace
) -> None:
    _, _, ws = seed_workspace
    project_id = str(uuid.uuid4())
    await _emit_env_separation_warning(
        db_session,
        workspace_id=ws.id,
        project_id=project_id,
        project_name="Demo",
    )
    await _emit_env_separation_warning(
        db_session,
        workspace_id=ws.id,
        project_id=project_id,
        project_name="Demo",
    )
    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(InboxItem.workspace_id == ws.id)
    )
    assert int(count or 0) == 0

    workspace = await db_session.get(Workspace, ws.id)
    pending = (workspace.settings or {}).get(ENV_SEPARATION_PENDING_KEY) or []
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_post_inbox_exception_skips_row(
    v1_client, seed_workspace, monkeypatch
) -> None:
    crumbs: list[dict] = []

    def _crumb(**kwargs):
        crumbs.append(kwargs)

    monkeypatch.setattr(
        agent_runs_routes,
        "record_inbox_exception_breadcrumb",
        _crumb,
    )

    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/items",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "type": "exception",
            "title": "probe exception",
            "body": "details",
            "ticket_ref": "ELS-144",
        },
    )
    assert res.status_code == 200, res.text
    assert "no inbox row" in (res.json().get("note") or "")

    count = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert count.json()["total"] == 0
    assert len(crumbs) == 1
