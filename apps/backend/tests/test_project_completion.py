"""Auto-complete / reopen projects based on child-ticket states.

``evaluate_project_completion`` flips a project's priority row to
``done`` (and Linear → Completed) when every child ticket is terminal
with at least one Done, and back to ``active`` when a done project gains
a non-terminal ticket. Empty / all-canceled projects are left alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.db.models.dashboard_priorities import WorkspaceProjectPriority
from backend.app.services.project_completion import evaluate_project_completion


def _gateway(issue_states: list[str], *, complete_raises: bool = False):
    calls: list[str] = []

    async def _get_project(pid, *, issues_limit=50):
        return {"id": pid, "issues": [{"state": s} for s in issue_states]}

    async def _complete_project(pid):
        calls.append(pid)
        if complete_raises:
            raise RuntimeError("linear statusId model")
        return True

    gw = SimpleNamespace(
        get_project=_get_project,
        complete_project=_complete_project,
    )
    gw.calls = calls  # type: ignore[attr-defined]
    return gw


async def _seed_priority(db_session, ws_id, project_id: str, state: str):
    row = WorkspaceProjectPriority(
        workspace_id=ws_id,
        project_native_id=project_id,
        ordinal=0,
        state=state,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
async def test_completes_when_all_tickets_done(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-1", "active")
    gw = _gateway(["Done", "Done", "Canceled"])
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=gw, project_id="proj-1"
    )
    assert res == "completed"
    assert gw.calls == ["proj-1"]  # Linear completed
    row = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.project_native_id == "proj-1"
            )
        )
    ).scalar_one()
    assert row.state == "done"
    assert row.completed_at is not None


@pytest.mark.asyncio
async def test_open_ticket_blocks_completion(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-2", "active")
    gw = _gateway(["Done", "In Progress"])
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=gw, project_id="proj-2"
    )
    assert res == "noop"
    assert gw.calls == []


@pytest.mark.asyncio
async def test_empty_project_not_completed(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-3", "active")
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=_gateway([]), project_id="proj-3"
    )
    assert res == "noop"


@pytest.mark.asyncio
async def test_all_canceled_no_done_not_completed(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-4", "active")
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=_gateway(["Canceled", "Canceled"]),
        project_id="proj-4",
    )
    assert res == "noop"


@pytest.mark.asyncio
async def test_reopens_done_project_with_new_ticket(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    row = await _seed_priority(db_session, ws.id, "proj-5", "done")
    from datetime import datetime, timezone
    row.completed_at = datetime.now(timezone.utc)
    await db_session.flush()
    gw = _gateway(["Done", "Todo"])  # a fresh ticket appeared
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=gw, project_id="proj-5"
    )
    assert res == "reopened"
    refreshed = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.project_native_id == "proj-5"
            )
        )
    ).scalar_one()
    assert refreshed.state == "active"
    assert refreshed.completed_at is None


@pytest.mark.asyncio
async def test_done_project_still_done_is_noop(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-6", "done")
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=_gateway(["Done", "Done"]),
        project_id="proj-6",
    )
    assert res == "noop"


@pytest.mark.asyncio
async def test_linear_failure_still_flips_priority(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_priority(db_session, ws.id, "proj-7", "active")
    gw = _gateway(["Done"], complete_raises=True)
    res = await evaluate_project_completion(
        db_session, workspace_id=ws.id, gateway=gw, project_id="proj-7"
    )
    # Linear flip is cosmetic — priority row still goes done.
    assert res == "completed"
    row = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.project_native_id == "proj-7"
            )
        )
    ).scalar_one()
    assert row.state == "done"
