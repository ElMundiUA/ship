"""Linear adapter ``ticket_list`` shape (ELS-83 + ELS-84).

Two invariants the agent picker depends on:

1. **``project_id``** — Linear projects each issue under exactly one
   project (or ``null``). The picker uses this to reject orphan
   tickets created outside the dashboard's ``_tool_create_project``
   flow. Without ``project_id`` in the projection, ELS-80 cannot
   filter by ``WorkspaceProjectPriority`` either.

2. **``labels``** — overlay labels (``needs:clarification`` /
   ``blocked``) freeze a ticket while a human owes a reply. The
   picker drops these so an agent doesn't burn a Cursor run on a
   ticket the operator is already triaging.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.integrations.linear.tracker_adapter import LinearTracker


@pytest.mark.asyncio
async def test_list_tickets_surfaces_project_id_and_labels() -> None:
    """The GraphQL projection pulls ``project { id }`` + ``labels``."""

    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        seen_queries.append(body)
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-uuid-1",
                                "identifier": "ELS-100",
                                "title": "Has project + labels",
                                "url": "https://linear.app/elship/issue/ELS-100/x",
                                "state": {"name": "Todo", "type": "unstarted"},
                                "project": {"id": "project-uuid-1"},
                                "labels": {
                                    "nodes": [
                                        {"name": "stage:wbs"},
                                        {"name": "needs:clarification"},
                                    ]
                                },
                                "updatedAt": "2026-05-06T10:00:00Z",
                            },
                            {
                                "id": "issue-uuid-2",
                                "identifier": "ELS-101",
                                "title": "Orphan — no project",
                                "url": "https://linear.app/elship/issue/ELS-101/x",
                                "state": {"name": "Todo", "type": "unstarted"},
                                "project": None,
                                "labels": {"nodes": []},
                                "updatedAt": "2026-05-06T11:00:00Z",
                            },
                        ]
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = LinearTracker(
            "lin_oauth_x",
            client=client,
            team_id="854ffe38-aaaa-bbbb-cccc-dddddddddddd",
            team_key="ELS",
        )
        rows = await tracker.list_tickets(state="all", limit=10)

    # Both issues come back; orphan distinction is in the project_id
    # field, not "missing from list".
    assert len(rows) == 2
    payload = json.loads(seen_queries[0])
    assert "project { id }" in payload["query"]
    assert "labels { nodes { name } }" in payload["query"]

    [first, second] = rows
    assert first["project_id"] == "project-uuid-1"
    assert first["labels"] == ["stage:wbs", "needs:clarification"]

    # Orphan: ``project_id`` is explicitly ``None`` (key present, value
    # null). The picker keys off this exact shape — key-present + null
    # value — to distinguish "tracker says no project" from "adapter
    # doesn't know how to surface project". Adapters that don't
    # surface project (Notion / Jira / GitHub Issues today) omit the
    # key entirely so the picker doesn't accidentally drop their rows.
    assert "project_id" in second
    assert second["project_id"] is None
    assert second["labels"] == []


@pytest.mark.asyncio
async def test_list_tickets_handles_unfiltered_query() -> None:
    """No filter (state=all, no team) still pulls project_id + labels."""

    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        seen_queries.append(body)
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-uuid-x",
                                "identifier": "ELS-99",
                                "title": "x",
                                "url": "https://linear.app/elship/issue/ELS-99/x",
                                "state": {"name": "Todo", "type": "unstarted"},
                                "project": {"id": "p-x"},
                                "labels": {"nodes": [{"name": "stage:wbs"}]},
                                "updatedAt": "2026-05-06T12:00:00Z",
                            }
                        ]
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # No team_id / team_key → unfiltered branch
        tracker = LinearTracker("lin_oauth_x", client=client)
        rows = await tracker.list_tickets(state="all", limit=10)

    assert len(rows) == 1
    payload = json.loads(seen_queries[0])
    assert "project { id }" in payload["query"]
    assert rows[0]["project_id"] == "p-x"
    assert rows[0]["labels"] == ["stage:wbs"]
