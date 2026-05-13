"""Unit tests for :meth:`LinearTracker.create_ticket` ``ticket_type``
mapping (ELS-69).

Linear has two rendering paths: native ``issueTypeId`` when the team
exposes the issue-types feature, otherwise a ``type:<value>`` label
that joins caller-supplied labels (de-duped). The probe is per-team
cached on the adapter instance so two sequential creates on the same
team share one round-trip.

We mock at the GraphQL boundary (``httpx.MockTransport``) so a single
``LinearTracker`` instance can drive multiple back-to-back calls
inside one test — that's how TC-12 (cache state) verifies the probe
only fires once.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from backend.app.integrations.linear.tracker_adapter import LinearTracker


def _gql_operation(body: dict[str, Any]) -> str:
    """Return the first non-blank line of the GraphQL query — the
    operation name we declared in the adapter (``ShipIssueTypes`` /
    ``ShipCreateIssue`` / ``ShipLabels``). Used to route the canned
    handler without parsing the full query."""
    for line in (body.get("query") or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


class _Handler:
    """Pluggable GraphQL handler that routes by operation prefix."""

    def __init__(
        self,
        issue_types: list[dict[str, Any]] | None = None,
        labels: list[dict[str, Any]] | None = None,
        issue_types_error: Exception | dict[str, Any] | None = None,
    ) -> None:
        self.issue_types = issue_types or []
        self.labels = labels or []
        self.issue_types_error = issue_types_error
        self.requests: list[dict[str, Any]] = []
        self.issue_type_probe_calls = 0
        self.create_payloads: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append(body)
        op = _gql_operation(body)
        if op.startswith("query ShipIssueTypes"):
            self.issue_type_probe_calls += 1
            if isinstance(self.issue_types_error, Exception):
                raise self.issue_types_error
            if self.issue_types_error is not None:
                return httpx.Response(200, json=self.issue_types_error)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "team": {
                            "issueTypes": {"nodes": self.issue_types}
                        }
                    }
                },
            )
        if op.startswith("query ShipLabels"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "team": {"labels": {"nodes": self.labels}}
                    }
                },
            )
        if op.startswith("mutation ShipCreateIssue"):
            self.create_payloads.append(body["variables"]["input"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid",
                                "identifier": "ELS-1",
                                "url": "https://linear.app/x/issue/ELS-1",
                            },
                        }
                    }
                },
            )
        return httpx.Response(
            500, json={"errors": [{"message": f"unhandled op {op!r}"}]}
        )


def _make_tracker(
    client: httpx.AsyncClient, *, team_id: str = "team-uuid"
) -> LinearTracker:
    return LinearTracker(
        "lin_oauth_x",
        client=client,
        team_id=team_id,
        team_key="ELS",
    )


@pytest.mark.asyncio
async def test_native_issue_type_matched_when_team_exposes_types() -> None:
    handler = _Handler(
        issue_types=[
            {"id": "it_bug", "name": "Bug"},
            {"id": "it_feature", "name": "Feature"},
            {"id": "it_task", "name": "Task"},
        ]
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(title="t", body="b", ticket_type="bug")
    assert len(handler.create_payloads) == 1
    payload = handler.create_payloads[0]
    assert payload["issueTypeId"] == "it_bug"
    # No fallback label injected when native field is set.
    assert "labelIds" not in payload


@pytest.mark.asyncio
async def test_no_match_falls_back_to_type_label() -> None:
    """Probe succeeds but no Linear native type matches the requested
    value — adapter falls through to the ``type:<value>`` label so the
    classification still lands. Caller-supplied labels are merged."""
    handler = _Handler(
        issue_types=[{"id": "it_initiative", "name": "Initiative"}],
        labels=[
            {"id": "lbl_urgent", "name": "urgent"},
            {"id": "lbl_type_feature", "name": "type:feature"},
        ],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(
            title="t",
            body="b",
            labels=["urgent"],
            ticket_type="feature",
        )
    payload = handler.create_payloads[0]
    assert "issueTypeId" not in payload
    # Both labels resolved (one caller-supplied, one fallback type label).
    assert set(payload["labelIds"]) == {"lbl_urgent", "lbl_type_feature"}


@pytest.mark.parametrize(
    "raised",
    [
        RuntimeError("Linear GraphQL error: Field 'issueTypes' undefined"),
        ValueError("schema error"),
        KeyError("issueTypes"),
    ],
)
@pytest.mark.asyncio
async def test_probe_schema_error_falls_back_to_label(raised) -> None:
    """Workspaces without Linear's issue-types feature raise on the
    probe rather than returning an empty list. ANY exception falls
    through to the label path so the call still succeeds."""
    handler = _Handler(
        issue_types_error=raised,
        labels=[{"id": "lbl_type_bug", "name": "type:bug"}],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(
            title="t", body="b", ticket_type="bug"
        )
    payload = handler.create_payloads[0]
    assert "issueTypeId" not in payload
    assert payload["labelIds"] == ["lbl_type_bug"]


@pytest.mark.asyncio
async def test_caller_supplied_type_label_deduped_on_fallback() -> None:
    """If the caller already passes ``type:bug`` in ``labels`` AND
    asks for ``ticket_type="bug"`` (with no native match), the
    adapter must NOT pass the label twice to ``_resolve_label_ids``."""
    handler = _Handler(
        issue_types=[],
        labels=[
            {"id": "lbl_type_bug", "name": "type:bug"},
            {"id": "lbl_ux", "name": "ux"},
        ],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(
            title="t",
            body="b",
            labels=["type:bug", "ux"],
            ticket_type="bug",
        )
    payload = handler.create_payloads[0]
    # Two unique labels — not three (no duplicate type:bug).
    assert sorted(payload["labelIds"]) == ["lbl_type_bug", "lbl_ux"]


@pytest.mark.asyncio
async def test_probe_cached_across_calls_same_team() -> None:
    """Two creates on the same ``LinearTracker`` instance share one
    issueTypes probe. The cache key is ``team_id`` only — a
    different ``ticket_type`` value must NOT re-trigger the probe."""
    handler = _Handler(
        issue_types=[
            {"id": "it_bug", "name": "Bug"},
            {"id": "it_task", "name": "Task"},
        ]
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(title="t1", body="b", ticket_type="bug")
        await tracker.create_ticket(title="t2", body="b", ticket_type="task")
    assert handler.issue_type_probe_calls == 1
    assert len(handler.create_payloads) == 2
    assert handler.create_payloads[0]["issueTypeId"] == "it_bug"
    assert handler.create_payloads[1]["issueTypeId"] == "it_task"


@pytest.mark.asyncio
async def test_documented_label_collision_is_accepted_behaviour() -> None:
    """BA TC-22: a Linear team that already uses ``type:bug`` for
    unrelated work shares the tag with agent-filed bug tickets. We
    DO NOT reject this case; we let the existing label resolve and
    the ticket take the existing label. If a future change wants to
    reject collisions, this test fails loudly so we re-litigate the
    BA non-goal rather than silently re-shaping behaviour."""
    handler = _Handler(
        issue_types=[],  # forces label path
        labels=[{"id": "lbl_type_bug", "name": "type:bug"}],
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        created = await tracker.create_ticket(
            title="t", body="b", ticket_type="bug"
        )
    payload = handler.create_payloads[0]
    assert payload["labelIds"] == ["lbl_type_bug"]
    assert created.display_id == "ELS-1"


@pytest.mark.asyncio
async def test_default_path_unchanged_when_ticket_type_omitted() -> None:
    """AC #2 no-drift gate: omitting ``ticket_type`` produces the
    exact same wire payload as before — no issueTypes probe, no
    ``type:*`` label, no ``issueTypeId`` field."""
    handler = _Handler()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = _make_tracker(client)
        await tracker.create_ticket(title="t", body="b")
    assert handler.issue_type_probe_calls == 0
    payload = handler.create_payloads[0]
    assert "issueTypeId" not in payload
    assert "labelIds" not in payload
