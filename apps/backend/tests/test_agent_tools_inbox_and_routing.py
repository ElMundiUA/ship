"""Tests for ``inbox_create`` + ``project_find_or_create``.

These tools back the reviewer-routing and report-routing redesign:

- ``inbox_create`` — supporting routines (daily-retro, learning-capture,
  process-reviewer) write a typed letter into the operator's mailbox
  instead of touching the repo or filing Linear noise.
- ``project_find_or_create`` — reviewers (tech / qa / security)
  funnel findings into a dedicated project per kind, idempotent on name
  so re-runs accumulate tickets in the same Linear project rather than
  spawning a fresh one each day.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select


class _StubProjectsTracker:
    """Captures list/create calls and lets tests pre-seed projects."""

    kind = "linear"

    def __init__(self, *, projects: list[dict[str, Any]] | None = None) -> None:
        self.projects: list[dict[str, Any]] = list(projects or [])
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    async def project_list(
        self,
        *,
        limit: int = 50,
        state: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        self.list_calls.append(
            {"limit": limit, "state": state, "query": query}
        )
        return list(self.projects)

    async def project_create(
        self,
        *,
        name: str,
        body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {"name": name, "body": body, "description": description}
        )
        new = {
            "id": f"proj-{len(self.create_calls)}",
            "name": name,
            "url": f"https://linear.app/elship/project/{name.lower()}",
            "slug": name.lower().replace(" ", "-"),
        }
        self.projects.append(new)
        return new

    # The create-project tool helper probes for an anchor; this tracker
    # doesn't model anchors, so the helper degrades to a no-op on
    # NotImplementedError — but ``project_find_or_create``
    # doesn't even call the anchor helper, so the stub doesn't need to
    # implement it.


def _toolbox(db_session, workspace, user):
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )


def _patch_tracker(toolbox, monkeypatch, stub: _StubProjectsTracker) -> None:
    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )


# ---------------------------------------------------------------------------
# project_find_or_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_or_create_returns_existing_match_case_insensitive(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    stub = _StubProjectsTracker(
        projects=[
            {"id": "p-tech", "name": "Tech Debt", "url": "u/1"},
            {"id": "p-qa", "name": "QA Debt", "url": "u/2"},
        ]
    )
    _patch_tracker(toolbox, monkeypatch, stub)

    out = await toolbox._tool_find_or_create_project_by_name(
        {
            "name": "tech debt",
            "body": "Holding pen for tech-debt findings.",
        }
    )
    payload = json.loads(out)
    assert payload["id"] == "p-tech"
    assert payload["created"] is False
    # MUST NOT have hit create.
    assert stub.create_calls == []


@pytest.mark.asyncio
async def test_find_or_create_makes_new_project_when_missing(
    db_session, seed_workspace, monkeypatch
) -> None:
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    stub = _StubProjectsTracker(projects=[])
    _patch_tracker(toolbox, monkeypatch, stub)

    out = await toolbox._tool_find_or_create_project_by_name(
        {
            "name": "Security",
            "body": "Daily security-officer findings land here.",
            "description": "Security findings (auto-routed).",
        }
    )
    payload = json.loads(out)
    assert payload["created"] is True
    assert payload["name"] == "Security"
    assert len(stub.create_calls) == 1
    assert stub.create_calls[0]["name"] == "Security"
    assert stub.create_calls[0]["description"] == "Security findings (auto-routed)."

    # Drafts row must be inserted so the freshly minted project shows up
    # on the dashboard immediately, same as plain ``project_create``.
    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert [r.project_native_id for r in rows] == [payload["id"]]
    assert rows[0].state == "planning"


@pytest.mark.asyncio
async def test_find_or_create_idempotent_on_repeat_call(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Calling twice with the same name must not double-create — the second
    call sees the freshly minted project in ``project_list`` and short-
    circuits."""
    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)
    stub = _StubProjectsTracker(projects=[])
    _patch_tracker(toolbox, monkeypatch, stub)

    first = json.loads(
        await toolbox._tool_find_or_create_project_by_name(
            {"name": "Tech Debt", "body": "Body."}
        )
    )
    second = json.loads(
        await toolbox._tool_find_or_create_project_by_name(
            {"name": "Tech Debt", "body": "Body."}
        )
    )
    assert first["created"] is True
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert len(stub.create_calls) == 1


# ---------------------------------------------------------------------------
# inbox_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_create_writes_report_item_with_body_in_payload(
    db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    out = await toolbox._tool_inbox_create(
        {
            "type": "report",
            "title": "Daily summary 2026-05-06",
            "body": "## Shipped\n- ELS-72\n- ELS-79\n\n## Open\n- ELS-80",
        }
    )
    payload = json.loads(out)
    assert payload["type"] == "report"
    assert payload["status"] == "new"

    rows = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    item = rows[0]
    assert item.type == "report"
    assert item.title == "Daily summary 2026-05-06"
    assert item.payload["body"].startswith("## Shipped")
    assert item.payload["source"] == "agent_tool"
    # Summary auto-derived from body when not provided.
    assert item.summary is not None
    assert item.summary.startswith("## Shipped")

    events = (
        await db_session.execute(
            select(InboxItemEvent).where(InboxItemEvent.item_id == item.id)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].action == "created"
    assert events[0].actor_kind == "agent"


@pytest.mark.asyncio
async def test_inbox_create_rejects_unknown_type(
    db_session, seed_workspace
) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    with pytest.raises(ToolInvocationError):
        await toolbox._tool_inbox_create(
            {
                "type": "failure",  # not in the agent-allowed enum
                "title": "x",
                "body": "y",
            }
        )


@pytest.mark.asyncio
async def test_inbox_create_explicit_summary_wins_over_body_prefix(
    db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    await toolbox._tool_inbox_create(
        {
            "type": "report",
            "title": "Retro",
            "summary": "3 wins, 1 blocker",
            "body": "Long markdown body here…" * 50,
        }
    )

    item = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == workspace.id
            )
        )
    ).scalar_one()
    assert item.summary == "3 wins, 1 blocker"
    assert item.payload["body"].startswith("Long markdown body")
    assert item.headline == "3 wins, 1 blocker"


@pytest.mark.asyncio
async def test_inbox_create_sets_headline_from_summary_first_line(
    db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    user, _, workspace = seed_workspace
    toolbox = _toolbox(db_session, workspace, user)

    await toolbox._tool_inbox_create(
        {
            "type": "report",
            "title": "Daily digest",
            "summary": "Yellow: 3 blockers\nLong body…",
            "body": "Full markdown digest",
        }
    )

    item = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == workspace.id
            )
        )
    ).scalar_one()
    assert item.headline == "Yellow: 3 blockers"
