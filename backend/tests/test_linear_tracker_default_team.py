"""Regression tests for ELS-71: Linear ``ticket_create`` default-team fallback.

Two problems before the fix:

1. ``_resolve_team_id(None)`` ignored ``self._team_id`` and re-queried
   Linear for "first team". On a workspace with multiple teams (the
   dogfood case — operator's Linear has both ``ELS`` and other teams)
   the call raised "Linear workspace has multiple teams; pass
   project_hint=...", even though the operator already configured the
   default during OAuth probe.

2. The Navigator's ``_resolve_tracker`` constructed ``LinearTracker(token)``
   without passing the integration row's ``config`` (which carries
   ``team_id`` / ``team_key``), so even if the resolver had a fallback
   the data was already gone by then.

This test pins the resolver behavior. The Navigator-side construction
path is exercised indirectly through the existing
``test_agent_tools_native_linear.py`` smoke test.
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.integrations.linear.tracker_adapter import LinearTracker


@pytest.mark.asyncio
async def test_create_ticket_uses_configured_team_id_when_no_hint() -> None:
    """``team_id`` from construction is used when no ``project_hint`` given."""

    seen_team_ids: list[str] = []
    queried_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried_paths.append(str(request.url))
        body = request.read().decode()
        # Bail loudly if the resolver tries to look up "first team" —
        # that's the bug. The configured ``team_id`` should short-circuit.
        assert "ShipFirstTeam" not in body, (
            f"resolver fell back to ShipFirstTeam despite configured team_id: {body}"
        )
        if "ShipCreateIssue" in body:
            # Pluck the team id off the input variables so the
            # assertion below catches a wrong value.
            import json as _json

            payload = _json.loads(body)
            seen_team_ids.append(payload["variables"]["input"]["teamId"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid",
                                "identifier": "ELS-99",
                                "url": "https://linear.app/elship/issue/ELS-99/x",
                            },
                        }
                    }
                },
            )
        return httpx.Response(404, json={"errors": [{"message": "unexpected"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = LinearTracker(
            "lin_oauth_x",
            client=client,
            team_id="854ffe38-aaaa-bbbb-cccc-dddddddddddd",
            team_key="ELS",
        )
        created = await tracker.create_ticket(
            title="Test ticket",
            body="body",
        )

    assert seen_team_ids == ["854ffe38-aaaa-bbbb-cccc-dddddddddddd"]
    assert created.display_id == "ELS-99"


@pytest.mark.asyncio
async def test_create_ticket_falls_back_to_team_key_when_only_key_set() -> None:
    """``team_key`` triggers a single resolve call, not the multi-team error."""

    seen_team_keys: list[str] = []
    seen_team_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "ShipFirstTeam" in body:
            raise AssertionError(
                "resolver fell back to first-team lookup despite team_key set"
            )
        if "ShipResolveTeam" in body:
            import json as _json

            payload = _json.loads(body)
            seen_team_keys.append(payload["variables"]["key"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {
                            "nodes": [
                                {"id": "854ffe38-aaaa-bbbb-cccc-dddddddddddd"},
                            ]
                        }
                    }
                },
            )
        if "ShipCreateIssue" in body:
            import json as _json

            payload = _json.loads(body)
            seen_team_ids.append(payload["variables"]["input"]["teamId"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid",
                                "identifier": "ELS-100",
                                "url": "https://linear.app/elship/issue/ELS-100/x",
                            },
                        }
                    }
                },
            )
        return httpx.Response(404, json={"errors": [{"message": "unexpected"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = LinearTracker(
            "lin_oauth_x",
            client=client,
            team_id=None,
            team_key="ELS",
        )
        await tracker.create_ticket(title="x", body="y")

    assert seen_team_keys == ["ELS"]
    assert seen_team_ids == ["854ffe38-aaaa-bbbb-cccc-dddddddddddd"]


@pytest.mark.asyncio
async def test_create_ticket_falls_through_to_first_team_when_unconfigured() -> None:
    """Single-team workspaces still work — only multi-team unconfigured fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "ShipFirstTeam" in body:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {
                            "nodes": [
                                {"id": "team-uuid", "key": "ONE"},
                            ]
                        }
                    }
                },
            )
        if "ShipCreateIssue" in body:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "issue-uuid",
                                "identifier": "ONE-1",
                                "url": "https://linear.app/x/issue/ONE-1/x",
                            },
                        }
                    }
                },
            )
        return httpx.Response(404, json={"errors": [{"message": "unexpected"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = LinearTracker("lin_oauth_x", client=client)
        created = await tracker.create_ticket(title="x", body="y")
    assert created.display_id == "ONE-1"


@pytest.mark.asyncio
async def test_create_ticket_raises_when_multi_team_and_unconfigured() -> None:
    """Honest error path: multiple teams + no default = ValueError."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "ShipFirstTeam" in body:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {
                            "nodes": [
                                {"id": "a", "key": "A"},
                                {"id": "b", "key": "B"},
                            ]
                        }
                    }
                },
            )
        return httpx.Response(404, json={"errors": [{"message": "unexpected"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tracker = LinearTracker("lin_oauth_x", client=client)
        with pytest.raises(ValueError, match="multiple teams"):
            await tracker.create_ticket(title="x", body="y")
