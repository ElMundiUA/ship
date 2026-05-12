"""Read-only Navigator tools added in PR-C1 of the tool review (ELS-78).

Three tools, all read-only and DB-backed:

- ``ticket_get(ticket_ref)`` — single-ticket lookup; routes through the
  bound tracker's ``get_ticket_snapshot``. Tested with a stub tracker
  so we don't need a live Linear connection.
- ``dashboard_get()`` — denormalised "what's on my plate?" payload
  composed from priorities + inbox + PRs + recent runs. Tested
  end-to-end with a seeded workspace + fixtures.
- ``audit_search`` — straight DB query against ``audit_log``
  with optional ``action`` / ``target_kind`` / ``target_id`` / ``since``
  filters. Tested with seeded rows.

The shape assertions are deliberately broad — these are denormalised
payloads whose copy may evolve. We pin only the load-bearing keys
that the agent will read.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def toolbox(db_session, seed_workspace):
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    return ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# ticket_get
# ---------------------------------------------------------------------------


class _StubTrackerWithSnapshot:
    """Minimal stub for the tracker resolution path in ``_tool_get_ticket``.

    The handler calls ``self._resolve_tracker(None, None)`` to get a
    tracker object, then ``tracker.get_ticket_snapshot(ref)``. Since
    ``_resolve_tracker`` queries DB integrations, we monkey-patch
    ``_resolve_tracker`` directly and skip the DB dance.
    """

    kind = "linear"

    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot
        self.calls = []

    async def get_ticket_snapshot(self, ref):
        self.calls.append(ref)
        return self.snapshot


@pytest.mark.asyncio
async def test_get_ticket_returns_snapshot(toolbox, monkeypatch) -> None:
    stub = _StubTrackerWithSnapshot(
        snapshot={
            "ticket_ref": "ELS-99",
            "title": "Test ticket",
            "description": "body",
            "url": "https://linear.app/elship/issue/ELS-99",
            "state": "In Progress",
            "labels": ["bug"],
            "project_id": "proj-uuid",
        }
    )

    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )

    raw = await toolbox._tool_get_ticket({"ticket_ref": "ELS-99"})
    payload = json.loads(raw)
    assert payload["ticket_ref"] == "ELS-99"
    assert payload["title"] == "Test ticket"
    assert payload["state"] == "In Progress"
    assert payload["project_id"] == "proj-uuid"
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_get_ticket_returns_not_found_when_missing(
    toolbox, monkeypatch
) -> None:
    stub = _StubTrackerWithSnapshot(snapshot=None)

    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )

    raw = await toolbox._tool_get_ticket({"ticket_ref": "ELS-9999"})
    payload = json.loads(raw)
    assert payload["error"] == "ticket_not_found"
    assert payload["ticket_ref"] == "ELS-9999"


@pytest.mark.asyncio
async def test_get_ticket_requires_ticket_ref(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(ToolInvocationError):
        await toolbox._tool_get_ticket({})


# ---------------------------------------------------------------------------
# dashboard_get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dashboard_returns_load_bearing_keys(
    toolbox, db_session, seed_workspace
) -> None:
    """Empty workspace returns the canonical shape with zero counts —
    the agent should be able to phrase a useful answer even without
    seeded data."""
    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)

    # Top-level keys that the agent will read
    for key in ("now", "priorities", "inbox", "pull_requests", "recent_activity"):
        assert key in payload, f"missing top-level key {key!r}"

    # Priorities buckets (always present, even if empty)
    for bucket in ("active", "planning", "parked"):
        assert bucket in payload["priorities"]
        assert isinstance(payload["priorities"][bucket], list)

    # Inbox shape
    assert "open_total" in payload["inbox"]
    assert "by_type" in payload["inbox"]
    assert "arrived_24h" in payload["inbox"]

    # PRs shape
    assert "open_total" in payload["pull_requests"]
    assert "shipped_24h" in payload["pull_requests"]


@pytest.mark.asyncio
async def test_get_dashboard_groups_priorities_by_state(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-active-1",
            ordinal=0,
            state="active",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-active-2",
            ordinal=1,
            state="active",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-draft",
            ordinal=2,
            state="planning",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-parked",
            ordinal=3,
            state="parked",
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)
    active_ids = [
        p["project_native_id"] for p in payload["priorities"]["active"]
    ]
    assert active_ids == ["proj-active-1", "proj-active-2"]
    assert [p["project_native_id"] for p in payload["priorities"]["planning"]] == [
        "proj-draft"
    ]
    assert [p["project_native_id"] for p in payload["priorities"]["parked"]] == [
        "proj-parked"
    ]


@pytest.mark.asyncio
async def test_get_dashboard_inbox_counts(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    _, _, workspace = seed_workspace
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="clarification",
            title="x1",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="clarification",
            title="x2",
            status="snoozed",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="approval",
            title="x3",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="failure",
            title="x4",
            status="dismissed",  # NOT counted (closed)
            intake_handle=None,
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)
    assert payload["inbox"]["open_total"] == 3
    assert payload["inbox"]["by_type"]["clarification"] == 2
    assert payload["inbox"]["by_type"]["approval"] == 1
    assert "failure" not in payload["inbox"]["by_type"]


# ---------------------------------------------------------------------------
# audit_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_search_returns_recent_rows(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    now = datetime.now(timezone.utc)

    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            actor_token_id=None,
            action="dashboard.priorities.reorder",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={"order_count": 3},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            actor_token_id=None,
            action="navigator.specialist_consult",
            target_kind="agent_role",
            target_id="designer",
            payload={"task_preview": "review the dashboard"},
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search({})
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "dashboard.priorities.reorder" in actions
    assert "navigator.specialist_consult" in actions
    assert payload["count"] == len(payload["audit_log"])


@pytest.mark.asyncio
async def test_audit_search_filters_by_action(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            action="dashboard.priorities.reorder",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            action="navigator.specialist_consult",
            target_kind="agent_role",
            target_id="ba",
            payload={},
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search(
        {"action": "navigator.specialist_consult"}
    )
    payload = json.loads(raw)
    assert all(
        r["action"] == "navigator.specialist_consult"
        for r in payload["audit_log"]
    )
    assert len(payload["audit_log"]) == 1


@pytest.mark.asyncio
async def test_audit_search_respects_since(
    toolbox, db_session, seed_workspace
) -> None:
    """Rows older than ``since`` must NOT come back. The default
    30-day window is enforced when ``since`` isn't provided."""
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    old_row = AuditLog(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        action="ancient.event",
        target_kind="workspace",
        target_id=str(workspace.id),
        payload={},
    )
    db_session.add(old_row)
    await db_session.flush()
    # Force the row's timestamp to 60 days ago — past the default
    # 30-day window.
    old_row.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search({})
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "ancient.event" not in actions

    # Explicit since=90 days back — old row comes back.
    raw = await toolbox._tool_workspace_audit_search(
        {"since": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()}
    )
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "ancient.event" in actions


@pytest.mark.asyncio
async def test_audit_search_validates_filter_types(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(ToolInvocationError, match="action must be a string"):
        await toolbox._tool_workspace_audit_search({"action": 42})
    with pytest.raises(
        ToolInvocationError, match="target_kind must be a string"
    ):
        await toolbox._tool_workspace_audit_search({"target_kind": 42})
