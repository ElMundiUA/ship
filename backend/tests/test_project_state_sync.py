"""Project-state ↔ ticket-state sync helper (Linear ELS-91).

Covers the helper's branching: skip paths (no project, no tracker,
unknown state, adapter without project listing), happy path
(transition each child + audit), partial failure (one transition
errors, others succeed), and the kind derivation fallback.

The helper takes a bare ``gateway`` and an :class:`AsyncSession`
mock so we don't need a live DB to exercise the branching. Stub
gateways implement the two methods the helper uses
(``list_project_tickets_in_state`` + ``transition``).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.agent.project_state_sync import (
    ProjectSyncReport,
    _derive_tracker_kind,
    sync_project_tickets_for_state,
)


class _StubGateway:
    """Minimal Linear-shaped adapter.

    Records every ``transition`` call so tests can assert on the
    sequence + arguments. ``raise_on`` lets a test simulate a
    per-ticket transition failure.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        raise_on_list: bool = False,
        raise_on_transition: set[str] | None = None,
    ) -> None:
        self._rows = rows
        self.raise_on_list = raise_on_list
        self.raise_on_transition = raise_on_transition or set()
        self.transitions: list[tuple[str, str]] = []

    async def list_project_tickets_in_state(
        self, *, project_id: str, linear_state_name: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        if self.raise_on_list:
            raise RuntimeError("simulated Linear 5xx")
        return self._rows

    async def transition(self, ticket, *, to_state: str) -> None:
        if ticket.id in self.raise_on_transition:
            raise RuntimeError(f"transition failed for {ticket.id}")
        self.transitions.append((ticket.id, to_state))


class _StubGatewayWithoutProjectList:
    """Adapter that doesn't surface the project-list method —
    helper must skip cleanly with ``adapter_no_project_list:<kind>``.
    """

    async def transition(self, ticket, *, to_state: str) -> None:
        raise AssertionError("transition should not be called on skip path")


class _Session:
    """Async-session stub with the methods the helper uses
    (``add`` is sync; ``flush`` is async). Records every audit row
    appended so tests can assert on the audit shape."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes: int = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1


def _ws() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-deadbeef0001")


def _user() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-cafef00d0001")


# ---------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_when_project_id_missing() -> None:
    session = _Session()
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id=None,
        new_state="active",
        gateway=_StubGateway([]),
        actor_user_id=_user(),
    )
    assert report.skipped_reason == "no_project_id"
    assert report.moved == 0 and report.errored == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_skip_when_no_tracker_bound() -> None:
    session = _Session()
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="active",
        gateway=None,
        actor_user_id=_user(),
    )
    assert report.skipped_reason == "no_tracker_bound"


@pytest.mark.asyncio
async def test_skip_for_unknown_state() -> None:
    session = _Session()
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="frobnicated",  # not in the transition plan
        gateway=_StubGateway([]),
        actor_user_id=_user(),
    )
    assert report.skipped_reason == "unknown_state:frobnicated"


@pytest.mark.asyncio
async def test_skip_when_adapter_lacks_project_list() -> None:
    """An adapter without ``list_project_tickets_in_state`` (Notion /
    Jira / GitHub Issues today) skips with a kind-tagged reason so
    the operator sees which adapter is the gap."""
    session = _Session()
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="active",
        gateway=_StubGatewayWithoutProjectList(),
        tracker_kind="notion",
        actor_user_id=_user(),
    )
    assert report.skipped_reason == "adapter_no_project_list:notion"


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_moves_backlog_to_todo() -> None:
    session = _Session()
    rows = [
        {"id": "tkt-uuid-1", "identifier": "ELS-100"},
        {"id": "tkt-uuid-2", "identifier": "ELS-101"},
    ]
    gateway = _StubGateway(rows)
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="active",
        gateway=gateway,
        tracker_kind="linear",
        actor_user_id=_user(),
    )
    assert report.moved == 2
    assert report.errored == 0
    assert report.skipped_reason is None
    assert report.moved_tickets == ["ELS-100", "ELS-101"]
    # Both transitioned to Todo (Active → Todo plan).
    assert gateway.transitions == [
        ("tkt-uuid-1", "Todo"),
        ("tkt-uuid-2", "Todo"),
    ]
    # One audit row per transition.
    assert len(session.added) == 2


@pytest.mark.asyncio
async def test_parked_moves_todo_to_backlog() -> None:
    session = _Session()
    gateway = _StubGateway(
        [{"id": "tkt-x", "identifier": "ELS-9"}]
    )
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-x",
        new_state="parked",
        gateway=gateway,
        actor_user_id=_user(),
    )
    assert report.moved == 1
    assert gateway.transitions == [("tkt-x", "Backlog")]


@pytest.mark.asyncio
async def test_planning_also_moves_todo_to_backlog() -> None:
    """Drafts (``planning``) shares the parked plan — child tickets
    in Todo go back to Backlog so they're not visible as queued
    work while the PO is still shaping."""
    session = _Session()
    gateway = _StubGateway([{"id": "x", "identifier": "ELS-1"}])
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-y",
        new_state="planning",
        gateway=gateway,
        actor_user_id=_user(),
    )
    assert gateway.transitions == [("x", "Backlog")]
    assert report.moved == 1


# ---------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_failure_returns_errored_report() -> None:
    session = _Session()
    gateway = _StubGateway([], raise_on_list=True)
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="active",
        gateway=gateway,
        actor_user_id=_user(),
    )
    assert report.errored == 1
    assert report.skipped_reason == "list_failed"
    # No audit rows — listing failed before any per-ticket work.
    assert session.added == []


@pytest.mark.asyncio
async def test_partial_transition_failure_continues_with_others() -> None:
    """One bad ticket doesn't take down the whole sync."""
    session = _Session()
    rows = [
        {"id": "ok-1", "identifier": "ELS-1"},
        {"id": "fail-1", "identifier": "ELS-2"},
        {"id": "ok-2", "identifier": "ELS-3"},
    ]
    gateway = _StubGateway(rows, raise_on_transition={"fail-1"})
    report = await sync_project_tickets_for_state(
        session,
        workspace_id=_ws(),
        project_id="proj-1",
        new_state="active",
        gateway=gateway,
        actor_user_id=_user(),
    )
    assert report.moved == 2
    assert report.errored == 1
    assert report.moved_tickets == ["ELS-1", "ELS-3"]
    # Audit: 2 success rows + 1 failure row = 3 total.
    assert len(session.added) == 3


# ---------------------------------------------------------------------
# Kind derivation
# ---------------------------------------------------------------------


def test_derive_kind_from_class_name() -> None:
    class LinearTracker:
        pass

    class NotionTracker:
        pass

    class CustomTracker:
        pass

    class WeirdShape:
        pass

    assert _derive_tracker_kind(LinearTracker()) == "linear"
    assert _derive_tracker_kind(NotionTracker()) == "notion"
    assert _derive_tracker_kind(CustomTracker()) == "custom"
    # No ``Tracker`` suffix → fall through to lowercased class name.
    assert _derive_tracker_kind(WeirdShape()) == "weirdshape"


def test_report_as_dict_shape() -> None:
    """The report's serialised shape is what the audit / tool
    response carries — pin the keys."""
    r = ProjectSyncReport(
        project_id="p",
        new_state="active",
        moved=2,
        errored=1,
        skipped_reason=None,
        moved_tickets=["ELS-1", "ELS-2"],
    )
    d = r.as_dict()
    assert set(d.keys()) == {
        "project_id",
        "new_state",
        "moved",
        "errored",
        "skipped_reason",
        "moved_tickets",
    }
