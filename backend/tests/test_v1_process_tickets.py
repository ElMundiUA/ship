from __future__ import annotations

import json

import pytest


class _FakeTracker:
    def __init__(self) -> None:
        self.comments = []
        self.transitions = []

    async def comment(self, ticket, *, body):  # noqa: ANN001
        self.comments.append({"ticket": ticket, "body": body})

    async def transition(self, ticket, *, to_state):  # noqa: ANN001
        self.transitions.append({"ticket": ticket, "to_state": to_state})


@pytest.mark.asyncio
async def test_process_tickets_lists_tracker_context_read_only(
    monkeypatch, v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.agent.tools import ToolBox

    _user, raw, workspace = seed_workspace
    captured: dict = {}

    async def _list_tickets(self, args):  # noqa: ANN001
        captured["workspace_id"] = self._workspace_id
        captured["args"] = args
        return json.dumps(
            {
                "tracker": "linear",
                "tickets": [
                    {
                        "id": "LIN-42",
                        "title": "Clarify checkout flow",
                        "url": "https://linear.app/acme/issue/LIN-42",
                        "status": "Todo",
                    }
                ],
            }
        )

    monkeypatch.setattr(ToolBox, "_tool_list_tickets", _list_tickets)

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/processes/development/tickets",
        headers={"Authorization": f"Bearer {raw}"},
        params={
            "tracker": "linear",
            "query": "checkout",
            "state": "open",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tracker"] == "linear"
    assert body["tickets"][0]["id"] == "LIN-42"
    assert captured["workspace_id"] == workspace.id
    assert captured["args"] == {
        "tracker": "linear",
        "project_hint": None,
        "state": "open",
        "query": "checkout",
        "assignee_me": False,
        "assignee": None,
        "limit": 5,
    }
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "process.ticket_picker.listed",
            )
        )
    ).scalar_one()
    assert audit.payload["query"] == "checkout"
    assert audit.payload["ticket_count"] == 1


@pytest.mark.asyncio
async def test_process_tickets_rejects_unknown_process(
    v1_client, seed_workspace
) -> None:
    _user, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/processes/unknown/tickets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_process_exit_clarification_posts_via_ship_and_audits(
    monkeypatch, v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.agent.tools import ToolBox

    _user, raw, workspace = seed_workspace
    fake = _FakeTracker()

    async def _resolve_tracker(self, preferred_kind, project_hint):  # noqa: ANN001
        assert preferred_kind == "linear"
        assert project_hint is None
        return fake

    monkeypatch.setattr(ToolBox, "_resolve_tracker", _resolve_tracker)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/processes/development/exits",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "type": "clarification",
            "state_id": "task_intake",
            "message": "Which checkout variant should we support first?",
            "ticket": {
                "kind": "linear",
                "id": "lin-uuid-42",
                "display_id": "LIN-42",
                "url": "https://linear.app/acme/issue/LIN-42",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["tracker_action"] == "comment"
    assert body["audit_action"] == "process.exit.clarification_posted"
    assert fake.comments[0]["ticket"].id == "lin-uuid-42"
    assert "@ship clarification:" in fake.comments[0]["body"]

    clarification = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert clarification.tracker_issue_key == "LIN-42"
    assert clarification.context["process_id"] == "development"

    inbox_item = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.source_id == clarification.id)
        )
    ).scalar_one_or_none()
    assert inbox_item is not None

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "process.exit.clarification_posted",
            )
        )
    ).scalar_one()
    assert audit.target_kind == "process"
    assert audit.payload["clarification_id"] == str(clarification.id)


@pytest.mark.asyncio
async def test_process_exit_handoff_validates_transition_and_audits_rejection(
    v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog

    _user, raw, workspace = seed_workspace
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/processes/development/exits",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "type": "handoff",
            "state_id": "task_intake",
            "to_state_id": "dev_implementation",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["audit_action"] == "process.exit.handoff_rejected"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "process.exit.handoff_rejected",
            )
        )
    ).scalar_one()
    assert audit.payload["from_state_id"] == "task_intake"
    assert audit.payload["to_state_id"] == "dev_implementation"
    assert audit.payload["reason"] == "transition_not_configured"


@pytest.mark.asyncio
async def test_process_exit_handoff_and_completion_contracts(
    monkeypatch, v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.agent.tools import ToolBox

    _user, raw, workspace = seed_workspace
    fake = _FakeTracker()

    async def _resolve_tracker(self, preferred_kind, project_hint):  # noqa: ANN001
        assert preferred_kind == "linear"
        return fake

    monkeypatch.setattr(ToolBox, "_resolve_tracker", _resolve_tracker)

    handoff = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/processes/development/exits",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "type": "handoff",
            "state_id": "task_intake",
            "to_state_id": "ba_requirements",
            "tracker_state": "In Requirements",
            "ticket": {"kind": "linear", "id": "lin-uuid-42"},
        },
    )
    assert handoff.status_code == 200, handoff.text
    assert handoff.json()["status"] == "accepted"
    assert handoff.json()["tracker_action"] == "transition"
    assert fake.transitions[0]["to_state"] == "In Requirements"

    completion = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/processes/development/exits",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "type": "complete_with_pr_or_result",
            "state_id": "ba_requirements",
            "pr_url": "https://github.com/acme/app/pull/42",
            "result_summary": "Requirements captured and PR opened.",
        },
    )
    assert completion.status_code == 200, completion.text
    assert completion.json()["status"] == "accepted"
    assert completion.json()["audit_action"] == "process.exit.completed"

    actions = (
        await db_session.execute(
            select(AuditLog.action)
            .where(AuditLog.workspace_id == workspace.id)
            .order_by(AuditLog.id)
        )
    ).scalars().all()
    assert "process.exit.handoff_completed" in actions
    assert "process.exit.completed" in actions
