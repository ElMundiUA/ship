"""P6 local executor server surface (ELS-247/248/249).

Pins the two endpoints ``shipctl local`` talks to:

- ``POST /local-executor/classify`` — escalation suggestion. Regex
  fast-path must work with NO LLM configured (the endpoint builds the
  service with ``client=None`` and the service degrades to NEUTRAL on
  the fallback path) — a missing suggestion must never break the
  local run.
- ``POST /tracker/tickets`` with ``project_id`` omitted — the a→b
  escalation files project-less tickets into the team's default
  backlog (ELS-249 relaxed the previously-required field).
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import AsyncMock

import pytest

from backend.app.api.v1.routes import agent_runs as agent_runs_routes
from backend.app.services.tracker_resolver import ResolvedTracker


class _FakeCreateGateway:
    def __init__(self) -> None:
        created = types.SimpleNamespace(
            display_id="ELS-999",
            url="https://linear.app/elship/issue/ELS-999",
            ref=types.SimpleNamespace(id=uuid.uuid4()),
        )
        self.create_ticket = AsyncMock(return_value=created)

    async def comment(self, _ref, *, body: str) -> None:
        return None


@pytest.fixture
def fake_create_tracker(monkeypatch):
    gateway = _FakeCreateGateway()
    resolved = ResolvedTracker(
        kind="memory",
        gateway=gateway,
        scope_hint=None,
        source="legacy",
    )

    async def _resolve(*_a, **_k):
        return resolved

    monkeypatch.setattr(agent_runs_routes, "resolve_for_workspace", _resolve)
    return gateway


@pytest.mark.asyncio
async def test_classify_escalate_fast_path_no_llm(
    db_session, v1_client, seed_workspace
) -> None:
    """Bilingual regex cues fire without any LLM key configured."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/local-executor/classify",
        headers={"Authorization": f"Bearer {raw}"},
        json={"ask": "это большая фича, заведи тикет и сделай PR"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verdict"] == "ESCALATE"
    assert body["reason"]


@pytest.mark.asyncio
async def test_classify_small_ask_neutral(
    db_session, v1_client, seed_workspace
) -> None:
    """Short non-matching asks stay NEUTRAL without an LLM call —
    and with no LLM configured the fallback path still degrades to
    NEUTRAL instead of erroring."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/local-executor/classify",
        headers={"Authorization": f"Bearer {raw}"},
        json={"ask": "tweak the hero copy"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["verdict"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_create_ticket_without_project_id(
    db_session, v1_client, seed_workspace, fake_create_tracker
) -> None:
    """ELS-249: escalation tickets carry no project anchor — the
    adapter lands them in the team's default backlog."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/tracker/tickets",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "title": "Escalated: rework billing exports",
            "body": "## Escalated from a local scratch session\n\nask…",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["ticket_ref"] == "ELS-999"
    kwargs = fake_create_tracker.create_ticket.await_args.kwargs
    assert kwargs["project_id"] is None
    assert kwargs["title"].startswith("Escalated:")
