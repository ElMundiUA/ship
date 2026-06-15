"""Mutating Navigator tools added in PR-C2 of the tool review (ELS-78).

Three admin-gated, audited tools:

- ``ticket_update(ticket_ref, title?, body?, labels?, state?)`` —
  edit an existing ticket on the bound tracker. Composes
  ``tracker.ticket_update`` (one ``issueUpdate`` covering title /
  body / labels) and ``tracker.transition`` for state changes.
- ``project_priority_set(project_native_id, state)`` — move a project
  between Active / Drafts (``planning`` enum) / Parked. Creates a
  priorities row at MAX+1 ordinal if none exists yet.
- ``decomposition_start(project_native_id)`` — walk a Drafts-bucket
  project's planning anchor into ``stage:decomposition`` to kick off the
  decomposition pipeline. Refuses if project is not in Drafts or
  has no anchor.

The tests use a stub tracker for adapter operations and seeded DB
rows for the priorities-side tools. Audit log insertion is verified.
"""

from __future__ import annotations

import json
import uuid

import pytest


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
# Stub tracker — covers ticket_update, transition, get_planning_anchor
# ---------------------------------------------------------------------------


class _StubTracker:
    """Captures every adapter call so tests can assert on them."""

    kind = "linear"

    def __init__(self, anchor=None) -> None:
        self.anchor = anchor
        self.update_calls: list[dict] = []
        self.transition_calls: list[dict] = []
        self.anchor_calls: list[str] = []

    async def ticket_update(self, ref, *, title=None, body=None, labels=None):
        self.update_calls.append(
            {
                "ref_id": ref.id,
                "title": title,
                "body": body,
                "labels": labels,
            }
        )

    async def transition(self, ref, *, to_state):
        self.transition_calls.append(
            {"ref_id": ref.id, "to_state": to_state}
        )

    async def get_planning_anchor(self, project_id):
        self.anchor_calls.append(project_id)
        return self.anchor


def _patch_tracker(toolbox, monkeypatch, stub: _StubTracker) -> None:
    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )


# ---------------------------------------------------------------------------
# ticket_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_ticket_combines_metadata_and_state(
    toolbox, monkeypatch, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog

    _, _, workspace = seed_workspace
    stub = _StubTracker()
    _patch_tracker(toolbox, monkeypatch, stub)

    raw = await toolbox._tool_update_ticket(
        {
            "ticket_ref": "ELS-99",
            "title": "Fixed title",
            "body": "Updated body",
            "labels": ["bug", "p2"],
            "state": "Done",
        }
    )
    payload = json.loads(raw)
    assert payload["ticket_ref"] == "ELS-99"
    assert "title" in payload["actions"]
    assert "body" in payload["actions"]
    assert "state:Done" in payload["actions"]

    # One update call covers title/body/labels; one transition call for state.
    assert len(stub.update_calls) == 1
    call = stub.update_calls[0]
    assert call["title"] == "Fixed title"
    assert call["body"] == "Updated body"
    assert call["labels"] == ["bug", "p2"]
    assert len(stub.transition_calls) == 1
    assert stub.transition_calls[0]["to_state"] == "Done"

    # Audit row landed.
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.ticket_update",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_id == "ELS-99"


@pytest.mark.asyncio
async def test_update_ticket_state_only_skips_metadata_call(
    toolbox, monkeypatch
) -> None:
    """A call with only ``state`` triggers the transition but NOT the
    metadata-update call — saves a round-trip when the agent just
    wants to close a ticket."""
    stub = _StubTracker()
    _patch_tracker(toolbox, monkeypatch, stub)

    raw = await toolbox._tool_update_ticket(
        {"ticket_ref": "ELS-1", "state": "Canceled"}
    )
    payload = json.loads(raw)
    assert payload["actions"] == ["state:Canceled"]
    assert stub.update_calls == []
    assert len(stub.transition_calls) == 1


@pytest.mark.asyncio
async def test_update_ticket_rejects_empty_call(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(
        ToolInvocationError, match="at least one of title"
    ):
        await toolbox._tool_update_ticket({"ticket_ref": "ELS-1"})


@pytest.mark.asyncio
async def test_update_ticket_rejects_bad_label_type(
    toolbox, monkeypatch
) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    _patch_tracker(toolbox, monkeypatch, _StubTracker())

    with pytest.raises(
        ToolInvocationError, match="labels must be a list of strings"
    ):
        await toolbox._tool_update_ticket(
            {"ticket_ref": "ELS-1", "labels": [1, 2, 3]}
        )


# ---------------------------------------------------------------------------
# project_priority_set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_priority_state_creates_row_when_missing(
    toolbox, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )
    from backend.app.db.models.tenancy import AuditLog

    _, _, workspace = seed_workspace

    raw = await toolbox._tool_set_priority_state(
        {"project_native_id": "proj-new", "state": "planning"}
    )
    payload = json.loads(raw)
    assert payload["project_native_id"] == "proj-new"
    assert payload["state"] == "planning"
    assert payload["prior_state"] is None

    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].project_native_id == "proj-new"
    assert rows[0].state == "planning"
    assert rows[0].ordinal == 0  # MAX+1 of an empty workspace = 0

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.project_priority_set",
            )
        )
    ).scalars().all()
    assert len(audit) == 1
    assert audit[0].payload["created_row"] is True


@pytest.mark.asyncio
async def test_set_priority_state_updates_existing_in_place(
    toolbox, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    _, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-x",
            ordinal=5,
            state="active",
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_set_priority_state(
        {"project_native_id": "proj-x", "state": "parked"}
    )
    payload = json.loads(raw)
    assert payload["state"] == "parked"
    assert payload["prior_state"] == "active"

    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ordinal == 5  # ordinal unchanged
    assert rows[0].state == "parked"


@pytest.mark.asyncio
async def test_set_priority_state_validates_enum(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(
        ToolInvocationError, match="state must be one of"
    ):
        await toolbox._tool_set_priority_state(
            {"project_native_id": "p", "state": "shipped"}
        )


# ---------------------------------------------------------------------------
# decomposition_start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_decomposition_transitions_anchor_to_decomposition(
    toolbox, monkeypatch, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )
    from backend.app.db.models.tenancy import AuditLog

    _, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-drafted",
            ordinal=0,
            state="planning",
        )
    )
    await db_session.flush()

    stub = _StubTracker(
        anchor={
            "id": "anchor-uuid",
            "identifier": "ELS-101",
            "url": "https://linear.app/elship/issue/ELS-101",
            "state": "Backlog",
        }
    )
    _patch_tracker(toolbox, monkeypatch, stub)

    # ELS-320: start fires maybe_dispatch inline so decomposition runs
    # without a manual kick. Capture the call instead of dispatching.
    from types import SimpleNamespace

    dispatch_calls: list[dict] = []

    async def _fake_dispatch(
        session, *, workspace_id, ticket_ref, trigger_kind, fsm_stage=None, **kw
    ):
        dispatch_calls.append(
            {"ticket_ref": ticket_ref, "fsm_stage": fsm_stage, "trigger_kind": trigger_kind}
        )
        return SimpleNamespace(fired=True)

    monkeypatch.setattr(
        "backend.app.services.dispatcher.maybe_dispatch", _fake_dispatch
    )

    raw = await toolbox._tool_decomposition_start(
        {"project_native_id": "proj-drafted"}
    )
    payload = json.loads(raw)
    assert payload["anchor_issue_id"] == "anchor-uuid"
    assert payload["anchor_identifier"] == "ELS-101"
    assert payload["process"] == "decomposition"
    assert payload["dispatched"] is True

    # Anchor probed; transition fired with stage:decomposition (E16/ELS-123 single-stage entry; ELS-308).
    assert stub.anchor_calls == ["proj-drafted"]
    assert len(stub.transition_calls) == 1
    assert stub.transition_calls[0]["to_state"] == "decomposition"
    assert stub.transition_calls[0]["ref_id"] == "anchor-uuid"

    # ELS-320: decomposition routine auto-dispatched inline, no kick.
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["fsm_stage"] == "decomposition"
    assert dispatch_calls[0]["ticket_ref"] == "ELS-101"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.start_decomposition",
            )
        )
    ).scalars().all()
    assert len(audit) == 1


@pytest.mark.asyncio
async def test_start_decomposition_rejects_non_drafts(
    toolbox, monkeypatch, db_session, seed_workspace
) -> None:
    """A project in Active or Parked must NOT hand off — only Drafts.
    The tool refuses with a clear message so the agent can phrase a
    useful response instead of silently leaving the project in a
    half-state."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )
    from backend.app.services.agent.tools import ToolInvocationError

    _, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-active",
            ordinal=0,
            state="active",
        )
    )
    await db_session.flush()
    _patch_tracker(toolbox, monkeypatch, _StubTracker())

    with pytest.raises(ToolInvocationError, match="only Drafts"):
        await toolbox._tool_decomposition_start(
            {"project_native_id": "proj-active"}
        )


@pytest.mark.asyncio
async def test_start_decomposition_404s_when_not_on_priorities(
    toolbox, monkeypatch
) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    _patch_tracker(toolbox, monkeypatch, _StubTracker())
    with pytest.raises(ToolInvocationError, match="not on the dashboard"):
        await toolbox._tool_decomposition_start(
            {"project_native_id": "proj-missing"}
        )


@pytest.mark.asyncio
async def test_start_decomposition_404s_when_anchor_missing(
    toolbox, monkeypatch, db_session, seed_workspace
) -> None:
    """Project predates the drafting flow (no anchor minted on
    create) — refuse and tell the agent why."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )
    from backend.app.services.agent.tools import ToolInvocationError

    _, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-no-anchor",
            ordinal=0,
            state="planning",
        )
    )
    await db_session.flush()
    _patch_tracker(toolbox, monkeypatch, _StubTracker(anchor=None))

    with pytest.raises(ToolInvocationError, match="no planning anchor"):
        await toolbox._tool_decomposition_start(
            {"project_native_id": "proj-no-anchor"}
        )
