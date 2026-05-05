"""Side-effects of the ``create_project`` tool (project-first delivery, ELS-73).

When the agent creates a project the side-effects ride along on the same
tool call:

- A ``WorkspaceProjectPriority`` row is inserted in ``state='planning'``
  (the dashboard surfaces this as the **Drafts** bucket) so the project
  is visible to the operator within one tool round-trip.
- A planning anchor issue is minted on the project so the future
  decomposition FSM has something to attach to.
- Both side-effects are idempotent on re-runs: the row is keyed on
  ``(workspace_id, project_native_id)``; the anchor check goes through
  ``tracker.get_planning_anchor`` first.
- Trackers that don't model anchors (Notion / future GitHub Issues) raise
  ``NotImplementedError`` and the tool degrades gracefully — the project
  itself was created and we don't roll that back over plumbing.

These tests exercise the helpers directly with a stub tracker so they
don't require a live Linear connection.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select


class _StubTracker:
    """Records every tracker call so the test can assert on side-effects.

    Configurable per-instance: ``existing_anchor`` short-circuits
    ``get_planning_anchor``; ``raise_on_get`` / ``raise_on_create``
    let tests emulate ``NotImplementedError`` paths or live-API errors.
    """

    def __init__(
        self,
        *,
        existing_anchor: dict[str, Any] | None = None,
        raise_on_get: type[BaseException] | None = None,
        raise_on_create: type[BaseException] | None = None,
    ) -> None:
        self.existing_anchor = existing_anchor
        self.raise_on_get = raise_on_get
        self.raise_on_create = raise_on_create
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    async def get_planning_anchor(
        self, project_id: str
    ) -> dict[str, Any] | None:
        self.get_calls.append(project_id)
        if self.raise_on_get is not None:
            raise self.raise_on_get(
                f"_StubTracker does not model planning anchors"
            )
        return self.existing_anchor

    async def create_planning_anchor(
        self,
        project_id: str,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {"project_id": project_id, "title": title, "body": body, "labels": labels}
        )
        if self.raise_on_create is not None:
            raise self.raise_on_create("_StubTracker create raised")
        return {
            "id": "anchor-uuid",
            "identifier": "STUB-1",
            "url": "https://stub.example/anchor-uuid",
        }


def _toolbox(
    db_session,
    workspace,
    user,
    *,
    thread_id=None,
    thread_intent=None,
):
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
        thread_id=thread_id,
        thread_intent=thread_intent,
    )


@pytest.mark.asyncio
async def test_drafts_priorities_row_inserts_then_idempotent(
    db_session, seed_workspace
) -> None:
    """First call inserts a Drafts (state='planning') row; second is a no-op."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    await toolbox._ensure_drafts_priorities_row(
        project_native_id="proj-x"
    )
    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert [r.project_native_id for r in rows] == ["proj-x"]
    assert rows[0].state == "planning"
    assert rows[0].ordinal == 0

    # Second call must not duplicate — keyed on (workspace, project_native_id).
    await toolbox._ensure_drafts_priorities_row(
        project_native_id="proj-x"
    )
    rows_after = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert len(rows_after) == 1
    assert rows_after[0].state == "planning"


@pytest.mark.asyncio
async def test_drafts_priorities_row_appends_after_existing_max(
    db_session, seed_workspace
) -> None:
    """A new project lands at MAX(ordinal)+1 so it doesn't reshuffle the queue."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="existing-active",
            ordinal=0,
            state="active",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="existing-parked",
            ordinal=5,
            state="parked",
        )
    )
    await db_session.flush()

    await toolbox._ensure_drafts_priorities_row(project_native_id="proj-new")

    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority)
            .where(WorkspaceProjectPriority.workspace_id == workspace.id)
            .order_by(WorkspaceProjectPriority.ordinal.asc())
        )
    ).scalars().all()
    assert [r.project_native_id for r in rows] == [
        "existing-active",
        "existing-parked",
        "proj-new",
    ]
    assert rows[-1].ordinal == 6
    assert rows[-1].state == "planning"


@pytest.mark.asyncio
async def test_planning_anchor_returns_existing_when_present(
    db_session, seed_workspace
) -> None:
    """Idempotency: a project that already has an anchor must NOT spawn a second."""
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    tracker = _StubTracker(
        existing_anchor={
            "id": "old-anchor",
            "identifier": "ELS-99",
            "url": "https://linear.app/elship/issue/ELS-99",
            "state": "Backlog",
        }
    )

    out = await toolbox._ensure_planning_anchor(
        tracker,  # type: ignore[arg-type]
        project_id="proj-x",
        project_name="Foo",
    )
    assert out is not None
    assert out["identifier"] == "ELS-99"
    assert tracker.get_calls == ["proj-x"]
    assert tracker.create_calls == []


@pytest.mark.asyncio
async def test_planning_anchor_creates_when_absent(
    db_session, seed_workspace
) -> None:
    """A project with no anchor yet gets one minted on the same call."""
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    tracker = _StubTracker(existing_anchor=None)

    out = await toolbox._ensure_planning_anchor(
        tracker,  # type: ignore[arg-type]
        project_id="proj-x",
        project_name="Export to PDF",
    )
    assert out is not None
    assert out["identifier"] == "STUB-1"
    assert len(tracker.create_calls) == 1
    call = tracker.create_calls[0]
    assert call["project_id"] == "proj-x"
    assert "Export to PDF" in call["title"]
    assert "Export to PDF" in call["body"]


@pytest.mark.asyncio
async def test_planning_anchor_returns_none_for_unsupported_tracker(
    db_session, seed_workspace
) -> None:
    """A tracker that doesn't model anchors degrades gracefully — the
    tool result skips the anchor block instead of lying."""
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    tracker = _StubTracker(raise_on_get=NotImplementedError)

    out = await toolbox._ensure_planning_anchor(
        tracker,  # type: ignore[arg-type]
        project_id="proj-x",
        project_name="Foo",
    )
    assert out is None
    # Did NOT proceed to create — get_planning_anchor's
    # NotImplementedError is the "this tracker can't" signal.
    assert tracker.create_calls == []


@pytest.mark.asyncio
async def test_drafts_row_stamps_originating_thread_when_provided(
    db_session, seed_workspace
) -> None:
    """When the toolbox knows the active thread (drafting mode) the new
    priorities row carries ``originating_thread_id`` so the dashboard
    can deep-link a "Continue shaping" affordance back to the
    conversation."""
    import uuid as _uuid
    from datetime import datetime, timezone

    from backend.app.db.models.agent_surface import ChatThread
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    thread = ChatThread(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Drafting a project",
        status="active",
        intent="shape_project",
        last_user_activity_at=datetime.now(timezone.utc),
    )
    db_session.add(thread)
    await db_session.flush()

    toolbox = _toolbox(
        db_session,
        workspace,
        user,
        thread_id=thread.id,
        thread_intent="shape_project",
    )
    await toolbox._ensure_drafts_priorities_row(
        project_native_id="proj-drafted"
    )

    row = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id,
                WorkspaceProjectPriority.project_native_id == "proj-drafted",
            )
        )
    ).scalar_one()
    assert row.originating_thread_id == thread.id
    assert row.state == "planning"


@pytest.mark.asyncio
async def test_drafts_row_backfills_thread_link_on_existing_row(
    db_session, seed_workspace
) -> None:
    """A project that already has a priorities row but no thread link
    (e.g. created from a default chat first) gets backfilled when the
    same project is touched from a drafting thread. The reverse path —
    overwriting an existing thread link — is rejected so deep-links
    don't drift."""
    from datetime import datetime, timezone

    from backend.app.db.models.agent_surface import ChatThread
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace

    # Pre-existing priorities row (no thread link).
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-x",
            ordinal=0,
            state="planning",
        )
    )
    await db_session.flush()

    thread = ChatThread(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Drafting a project",
        status="active",
        intent="shape_project",
        last_user_activity_at=datetime.now(timezone.utc),
    )
    db_session.add(thread)
    await db_session.flush()

    toolbox = _toolbox(
        db_session, workspace, user, thread_id=thread.id
    )
    await toolbox._ensure_drafts_priorities_row(
        project_native_id="proj-x"
    )

    row = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id,
                WorkspaceProjectPriority.project_native_id == "proj-x",
            )
        )
    ).scalar_one()
    assert row.originating_thread_id == thread.id

    # Second drafting thread — the link must NOT change. First
    # thread wins so the deep-link doesn't drift.
    second_thread = ChatThread(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Different drafting",
        status="active",
        intent="shape_project",
        last_user_activity_at=datetime.now(timezone.utc),
    )
    db_session.add(second_thread)
    await db_session.flush()

    toolbox2 = _toolbox(
        db_session, workspace, user, thread_id=second_thread.id
    )
    await toolbox2._ensure_drafts_priorities_row(
        project_native_id="proj-x"
    )
    await db_session.refresh(row)
    assert row.originating_thread_id == thread.id


@pytest.mark.asyncio
async def test_planning_anchor_swallows_create_errors(
    db_session, seed_workspace
) -> None:
    """A live-API failure on create_planning_anchor must NOT roll back the
    project — the project itself was created upstream and the anchor is
    best-effort plumbing."""
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    tracker = _StubTracker(raise_on_create=RuntimeError)

    out = await toolbox._ensure_planning_anchor(
        tracker,  # type: ignore[arg-type]
        project_id="proj-x",
        project_name="Foo",
    )
    assert out is None
    assert len(tracker.create_calls) == 1
