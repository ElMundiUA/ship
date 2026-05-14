"""Unit tests for the MemoryTracker adapter (E19).

Pins the laptop-offline TrackerGateway implementation against the
behaviour the orchestrator + Console rely on:

- FSM-stage filter on ``list_tickets`` (state="task_intake" returns
  open tickets tagged ``stage:task_intake`` only)
- ``transition`` swaps the ``stage:*`` label without bumping the
  display state
- ``create_ticket`` mints workspace-scoped serial display ids
- ``upsert_project_section`` replaces a named ``## section`` block
  in the project body and appends a new section at the end
- Cross-workspace isolation: tickets in workspace A are never
  visible to workspace B even on the same Postgres
"""

from __future__ import annotations

import pytest

from backend.app.db.models.memory_adapters import (
    MemoryTrackerProject,
    MemoryTrackerTicket,
)
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.local.tracker import MemoryTracker


# ---------------------------------------------------------------------------
# list_tickets — state filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickets_fsm_stage_filter(db_session, seed_workspace):
    """``state="task_intake"`` returns only open tickets carrying
    the ``stage:task_intake`` label."""
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    await tr.create_ticket(
        title="match",
        body="b",
        labels=["stage:task_intake"],
    )
    await tr.create_ticket(
        title="wrong-stage",
        body="b",
        labels=["stage:ba_requirements"],
    )
    await tr.create_ticket(title="no-stage", body="b", labels=[])
    await db_session.commit()

    rows = await tr.list_tickets(state="task_intake", limit=10)
    titles = {r["title"] for r in rows}
    assert titles == {"match"}


@pytest.mark.asyncio
async def test_list_tickets_open_filter(db_session, seed_workspace):
    """``state="open"`` excludes Done/Cancelled rows."""
    from sqlalchemy import select as sa_select

    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    await tr.create_ticket(title="alive", body="b")
    closed = await tr.create_ticket(title="closed", body="b")
    await db_session.commit()

    # Workspace-scoped lookup — display_ids only collide if the
    # MAX(serial) read missed a flushed insert, which would already
    # be a regression.
    row = (
        await db_session.execute(
            sa_select(MemoryTrackerTicket).where(
                MemoryTrackerTicket.workspace_id == workspace.id,
                MemoryTrackerTicket.display_id == closed.display_id,
            )
        )
    ).scalar_one()
    row.state = "Done"
    await db_session.commit()

    rows = await tr.list_tickets(state="open", limit=10)
    assert {r["title"] for r in rows} == {"alive"}


# ---------------------------------------------------------------------------
# transition — FSM label vs display state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_swaps_stage_label(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    created = await tr.create_ticket(
        title="x",
        body="b",
        labels=["stage:task_intake"],
    )
    await db_session.commit()

    await tr.transition(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace.id),
            id=created.display_id,
        ),
        to_state="ba_requirements",
    )
    await db_session.commit()

    rows = await tr.list_tickets(state="ba_requirements", limit=5)
    labels = rows[0]["labels"]
    assert "stage:ba_requirements" in labels
    assert "stage:task_intake" not in labels
    # display state untouched
    assert rows[0]["status"] == "Todo"


@pytest.mark.asyncio
async def test_transition_to_display_state(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    created = await tr.create_ticket(title="x", body="b")
    await db_session.commit()

    await tr.transition(
        TicketRef(
            kind="linear",
            workspace_hint=str(workspace.id),
            id=created.display_id,
        ),
        to_state="In Progress",
    )
    await db_session.commit()

    rows = await tr.list_tickets(state="open", limit=5)
    assert rows[0]["status"] == "In Progress"


# ---------------------------------------------------------------------------
# Cross-workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tickets_isolated_per_workspace(db_session, seed_user):
    """Two workspaces in the same Postgres see only their own tickets."""
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember
    import uuid

    user, org = seed_user
    ws_a = Workspace(org_id=org.id, slug=f"a-{uuid.uuid4().hex[:6]}", name="A")
    ws_b = Workspace(org_id=org.id, slug=f"b-{uuid.uuid4().hex[:6]}", name="B")
    db_session.add_all([ws_a, ws_b])
    await db_session.flush()
    db_session.add_all(
        [
            WorkspaceMember(
                workspace_id=ws_a.id, user_id=user.id, role="owner",
                answer_specialist_slugs=["*"],
            ),
            WorkspaceMember(
                workspace_id=ws_b.id, user_id=user.id, role="owner",
                answer_specialist_slugs=["*"],
            ),
        ]
    )
    await db_session.flush()

    tracker_a = MemoryTracker(session=db_session, workspace_id=ws_a.id)
    tracker_b = MemoryTracker(session=db_session, workspace_id=ws_b.id)
    await tracker_a.create_ticket(title="A-only", body="b")
    await tracker_b.create_ticket(title="B-only", body="b")
    await db_session.commit()

    rows_a = await tracker_a.list_tickets(limit=10)
    rows_b = await tracker_b.list_tickets(limit=10)
    assert {r["title"] for r in rows_a} == {"A-only"}
    assert {r["title"] for r in rows_b} == {"B-only"}


# ---------------------------------------------------------------------------
# Projects — section upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_project_section_replaces_existing(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    proj = await tr.create_project(
        name="P",
        body="## WBS\n\nold WBS\n\n## Architecture\n\nold arch\n",
    )
    await db_session.commit()
    await tr.upsert_project_section(proj["id"], section="WBS", body="new WBS")
    await db_session.commit()

    fetched = await tr.get_project(proj["id"])
    assert "new WBS" in fetched["content"]
    assert "old WBS" not in fetched["content"]
    # Architecture section preserved.
    assert "old arch" in fetched["content"]


@pytest.mark.asyncio
async def test_upsert_project_section_appends_when_missing(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    proj = await tr.create_project(name="P", body="## WBS\n\nrows\n")
    await db_session.commit()
    await tr.upsert_project_section(
        proj["id"], section="Tasks", body="task list"
    )
    await db_session.commit()

    fetched = await tr.get_project(proj["id"])
    assert "## Tasks" in fetched["content"]
    assert "task list" in fetched["content"]


# ---------------------------------------------------------------------------
# Planning anchor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planning_anchor_create_and_fetch(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    proj = await tr.create_project(name="P", body="body")
    await db_session.commit()

    assert await tr.get_planning_anchor(proj["id"]) is None
    anchor = await tr.create_planning_anchor(
        proj["id"], title="Plan P", body="anchor body"
    )
    await db_session.commit()
    assert anchor["identifier"].startswith("MEM-")

    second = await tr.get_planning_anchor(proj["id"])
    assert second is not None
    assert second["identifier"] == anchor["identifier"]

    # Idempotency — a second create returns the same anchor.
    same = await tr.create_planning_anchor(
        proj["id"], title="ignored", body="ignored"
    )
    assert same["identifier"] == anchor["identifier"]


# ---------------------------------------------------------------------------
# Display id is workspace-scoped + monotonic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_display_ids_are_monotonic_per_workspace(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    a = await tr.create_ticket(title="a", body="b")
    b = await tr.create_ticket(title="b", body="b")
    c = await tr.create_ticket(title="c", body="b")
    await db_session.commit()
    assert (a.display_id, b.display_id, c.display_id) == (
        "MEM-1",
        "MEM-2",
        "MEM-3",
    )


# ---------------------------------------------------------------------------
# Comments + list_issues_with_label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_comment_and_list_comments(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    created = await tr.create_ticket(title="x", body="b")
    await db_session.commit()
    ref = TicketRef(
        kind="linear",
        workspace_hint=str(workspace.id),
        id=created.display_id,
    )
    await tr.comment(ref, body="first")
    await tr.comment(ref, body="second")
    await db_session.commit()

    comments = await tr.list_comments(ref)
    assert [c.body for c in comments] == ["first", "second"]


@pytest.mark.asyncio
async def test_list_issues_with_label_and_remove(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    tr = MemoryTracker(session=db_session, workspace_id=workspace.id)
    created = await tr.create_ticket(
        title="x",
        body="b",
        labels=["ship:needs-clarification"],
    )
    await db_session.commit()

    hits = await tr.list_issues_with_label("ship:needs-clarification")
    assert [h.display_id for h in hits] == [created.display_id]

    ref = TicketRef(
        kind="linear",
        workspace_hint=str(workspace.id),
        id=created.display_id,
    )
    await tr.remove_label(ref, "ship:needs-clarification")
    await db_session.commit()
    hits = await tr.list_issues_with_label("ship:needs-clarification")
    assert hits == []
