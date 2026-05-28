"""Phase 3 of the FSM event-driven rearchitecture — after PR merge,
walk the workspace's work queue automatically.

The PR-merge handler in ``github_app.py`` calls
``_dispatch_next_eligible_ticket``. It must:

1. Pick the first Backlog ticket in the same Linear project as the
   just-merged ticket, transition it to entry-stage ``planning``,
   and fire ``maybe_dispatch`` with ``trigger_kind="project_next"``.
2. Fall back to the next Linear project (state ``started`` →
   ``planned``) when the current project has no Backlog tickets left.
3. Write ``dispatch.queue_idle`` and stop firing dispatches when no
   candidate exists in any project. NO cron, NO human touch required
   to recover — the queue stays idle until something genuinely
   enqueues new work.

Pinned by per-scenario unit tests against a recording fake gateway
that captures every ``transition`` + ``maybe_dispatch`` call.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.v1.routes.github_app import (
    _dispatch_next_eligible_ticket,
)


class _FakeGateway:
    """Recording gateway stand-in — captures everything the helper calls."""

    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []  # (ticket_id, to_state)
        self.project_tickets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.projects_by_state: dict[str, list[dict[str, Any]]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}

    async def get_ticket_snapshot(self, ref):
        return self.snapshots.get(ref.id)

    async def list_project_tickets_in_state(
        self, *, project_id: str, linear_state_name: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        return list(
            self.project_tickets.get((project_id, linear_state_name), [])
        )[: limit]

    async def list_projects(
        self, *, limit: int = 50, state: str | None = None, query=None
    ) -> list[dict[str, Any]]:
        return list(self.projects_by_state.get(state or "", []))[:limit]

    async def transition(self, ref, *, to_state: str, from_state=None) -> None:
        self.transitions.append((ref.id, to_state))


def _build_resolved(gateway: _FakeGateway):
    obj = AsyncMock()
    obj.kind = "linear"
    obj.gateway = gateway
    return obj


@pytest.fixture
def workspace_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def session():
    s = AsyncMock()
    # capture rows passed to session.add for audit assertions
    s.added = []
    s.add = lambda row: s.added.append(row)
    return s


def _resolve_patch(gw: _FakeGateway):
    """Patch resolve_for_workspace to return our recording gateway.

    The helper imports resolve_for_workspace lazily inside its body
    (``from backend.app.services.tracker_resolver import resolve_for_workspace``)
    so we patch at the source module — the import inside the helper
    binds to our mocked symbol every call.
    """
    return patch(
        "backend.app.services.tracker_resolver.resolve_for_workspace",
        new=AsyncMock(return_value=_build_resolved(gw)),
    )


def _dispatch_patch(captured: list, fired: bool = True, reason: str = "fired"):
    """Patch maybe_dispatch + return a list to capture call kwargs."""
    async def _fn(session, **kwargs):
        captured.append(kwargs)
        result = AsyncMock()
        result.fired = fired
        result.reason = reason
        return result

    return patch(
        "backend.app.services.dispatcher.maybe_dispatch",
        new=_fn,
    )


@pytest.mark.asyncio
async def test_same_project_pick_when_backlog_has_siblings(workspace_id, session):
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": "proj-A"}
    gw.project_tickets[("proj-A", "Backlog")] = [
        {"identifier": "ELS-201", "title": "next one"},
        {"identifier": "ELS-202", "title": "after that"},
    ]
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): done",
        )
    # Pick the first Backlog ticket; transition it; dispatch.
    assert gw.transitions == [("ELS-201", "planning")]
    assert len(captured) == 1
    call = captured[0]
    assert call["ticket_ref"] == "ELS-201"
    assert call["trigger_kind"] == "project_next"
    assert call["fsm_stage"] == "planning"
    # Audit row records the advance.
    actions = [getattr(r, "action", None) for r in session.added]
    assert "dispatch.queue_advance" in actions


@pytest.mark.asyncio
async def test_next_epic_pick_when_current_project_empty(workspace_id, session):
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": "proj-A"}
    # Same project Backlog is empty (e.g., just-merged ticket was the
    # only one). Fall back to the next project — list_projects returns
    # newest first per Linear adapter contract.
    gw.project_tickets[("proj-A", "Backlog")] = []
    gw.projects_by_state["started"] = [
        {"id": "proj-A", "name": "current — should be skipped"},
        {"id": "proj-B", "name": "next epic"},
    ]
    gw.project_tickets[("proj-B", "Backlog")] = [
        {"identifier": "PAC-9", "title": "first of next epic"},
    ]
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): last",
        )
    assert gw.transitions == [("PAC-9", "planning")]
    assert captured[0]["ticket_ref"] == "PAC-9"
    assert captured[0]["trigger_kind"] == "next_epic"
    # Advance audit captures the cross-project hop.
    advance_rows = [
        r for r in session.added
        if getattr(r, "action", None) == "dispatch.queue_advance"
    ]
    assert len(advance_rows) == 1
    payload = advance_rows[0].payload
    assert payload["via"] == "next_epic"
    assert payload["merged_project_id"] == "proj-A"
    assert payload["next_project_id"] == "proj-B"


@pytest.mark.asyncio
async def test_started_then_planned_fallback(workspace_id, session):
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": "proj-A"}
    gw.project_tickets[("proj-A", "Backlog")] = []
    # Active queue is empty; pull from the planned bucket — common when
    # the operator drafted a project but hasn't started it yet.
    gw.projects_by_state["started"] = [
        {"id": "proj-A", "name": "current, no Backlog"},
    ]
    gw.projects_by_state["planned"] = [
        {"id": "proj-Z", "name": "next planned epic"},
    ]
    gw.project_tickets[("proj-Z", "Backlog")] = [
        {"identifier": "PAC-50", "title": "first task"},
    ]
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): x",
        )
    assert captured[0]["ticket_ref"] == "PAC-50"
    assert captured[0]["trigger_kind"] == "next_epic"


@pytest.mark.asyncio
async def test_queue_idle_when_nothing_to_dispatch(workspace_id, session):
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": "proj-A"}
    # No Backlog in current; no projects in started/planned; nothing
    # to do. Helper must NOT silently exit — it must write
    # dispatch.queue_idle so the operator can see "we truly have no
    # work" instead of suspecting a missed event.
    gw.project_tickets[("proj-A", "Backlog")] = []
    gw.projects_by_state["started"] = []
    gw.projects_by_state["planned"] = []
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): last",
        )
    assert captured == []  # zero dispatches
    assert gw.transitions == []
    actions = [getattr(r, "action", None) for r in session.added]
    assert "dispatch.queue_idle" in actions
    # Idle row carries enough context to diagnose later
    idle = [
        r for r in session.added
        if getattr(r, "action", None) == "dispatch.queue_idle"
    ][0]
    assert idle.payload["current_project_id"] == "proj-A"
    assert idle.payload["reason"] == "no_backlog_tickets"


@pytest.mark.asyncio
async def test_same_project_pick_ignores_self_loop(workspace_id, session):
    # Defensive: the just-merged ticket might still appear in the
    # Backlog list result if the Linear adapter / replica hasn't caught
    # up with the Done transition we wrote a millisecond ago. The helper
    # must NOT re-dispatch the same ticket — that would yo-yo the chain
    # back into planning right after it finished auto-merge.
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": "proj-A"}
    gw.project_tickets[("proj-A", "Backlog")] = [
        {"identifier": "ELS-200", "title": "the one we just merged"},
        {"identifier": "ELS-201", "title": "the real next one"},
    ]
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): done",
        )
    assert captured[0]["ticket_ref"] == "ELS-201"
    assert ("ELS-200", "planning") not in gw.transitions


@pytest.mark.asyncio
async def test_unparseable_pr_title_is_silent_noop(workspace_id, session):
    gw = _FakeGateway()
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="chore: bump deps",
        )
    # No ticket ref → no project to look in → nothing to do. Don't write
    # an audit row for this case; it's not "queue empty", it's "merge
    # wasn't ticket-linked".
    assert captured == []
    assert gw.transitions == []
    assert session.added == []


@pytest.mark.asyncio
async def test_orphan_ticket_without_project_is_silent_noop(workspace_id, session):
    # Ticket exists but has no Linear project assigned. Phase 3 has no
    # "same epic" concept here, so it skips. (Orphans are also filtered
    # out by the picker upstream — this is just defense in depth.)
    gw = _FakeGateway()
    gw.snapshots["ELS-200"] = {"project_id": None}
    captured: list = []
    with _resolve_patch(gw), _dispatch_patch(captured):
        await _dispatch_next_eligible_ticket(
            session, workspace_id=workspace_id, pr_title="fix(ELS-200): x",
        )
    assert captured == []
    assert session.added == []
