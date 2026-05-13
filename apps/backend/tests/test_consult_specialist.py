"""``specialist_consult`` subagent tool — Navigator overhaul PR3.

The Navigator can now hand a focused task off to a specialist
subagent (designer / tech-architect / qa-architect / ba / bug-triage
/ developer-as-researcher). The subagent runs as an isolated
``astream → tool calls → astream`` loop with the role's prompt as
its system message and the same workspace tool surface MINUS the
``specialist_consult`` tool itself (no recursion). It returns one
final report which the parent Navigator then consumes.

These tests cover the load-bearing invariants without requiring a
live LLM: a stub ``AgentClient`` scripts pre-recorded events, the
``pick_default_client`` factory is monkey-patched to hand back the
stub, and the handler's behaviour is asserted against deterministic
event sequences. The actual LLM integration is verified separately
via the live-deploy smoke checklist.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Stub agent client — the only "moving part" the tests script
# ---------------------------------------------------------------------------


class _StubAgentClient:
    """Hand-rolled ``AgentClient`` that yields pre-scripted events.

    Each entry in ``scripts`` is a list of events produced on one
    ``astream`` round-trip. The stub records every call so tests can
    assert on what the subagent loop sent (system prompt, tools list,
    message stack growth across turns).
    """

    vendor = "stub"

    def __init__(self, scripts: list[list[Any]]) -> None:
        self._remaining = list(scripts)
        self.calls: list[dict[str, Any]] = []

    async def astream(self, messages, tools=(), **_kwargs):  # noqa: ANN001
        self.calls.append(
            {
                "messages": [
                    {"role": m.role, "content": m.content, "name": m.name}
                    for m in messages
                ],
                "tool_names": [t.name for t in tools],
            }
        )
        events = self._remaining.pop(0) if self._remaining else []

        async def _gen():
            for e in events:
                yield e

        return _gen()

    async def acomplete(self, *_args, **_kwargs):  # pragma: no cover
        return ""


def _patch_client(monkeypatch, stub: _StubAgentClient) -> None:
    """Wire the stub in front of ``pick_default_client`` so the
    subagent loop talks to it instead of a live vendor."""
    monkeypatch.setattr(
        "backend.app.services.agent.client.pick_default_client",
        lambda settings: stub,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def toolbox(db_session, seed_workspace):
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    return ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# Spec / argument validation
# ---------------------------------------------------------------------------


def test_spec_is_present_and_advertises_allowed_specialists(
    db_session, seed_workspace
) -> None:
    """The Navigator's tool list MUST include ``specialist_consult``
    and the spec's ``specialist`` enum MUST match the handler's
    allowlist verbatim — drift would let the LLM emit a slug the
    handler rejects, which surfaces as a useless ``unknown
    specialist`` error to the model."""
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    tb = ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )
    specs = tb.specs()
    by_name = {s.name: s for s in specs}
    assert "specialist_consult" in by_name
    enum = by_name["specialist_consult"].parameters["properties"]["specialist"][
        "enum"
    ]
    assert sorted(enum) == sorted(tb._SUBAGENT_ALLOWED_SPECIALISTS)


@pytest.mark.asyncio
async def test_handler_rejects_unknown_specialist(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(ToolInvocationError, match="unknown specialist"):
        await toolbox._tool_consult_specialist(
            {"specialist": "fortune-teller", "task": "tell me the future"}
        )


@pytest.mark.asyncio
async def test_handler_rejects_when_subagent_active(toolbox) -> None:
    """Recursion guard: if a stray ``specialist_consult`` invocation
    fires while ``_subagent_active`` is True (defensive — the spec is
    filtered out of the subagent's tool list, so this only fires on
    LLM hallucination), the handler must refuse loudly."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox._subagent_active = True
    with pytest.raises(ToolInvocationError, match="nested specialist_consult"):
        await toolbox._tool_consult_specialist(
            {"specialist": "ba", "task": "anything"}
        )


# ---------------------------------------------------------------------------
# Subagent loop — happy path + tool loop + caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_returns_text_when_model_stops_immediately(
    toolbox, monkeypatch
) -> None:
    """First (and only) astream turn produces text + no tool calls.
    The handler returns the report text wrapped in JSON for the
    parent. Audit row records the call."""
    from backend.app.services.agent.client import End, TextDelta

    stub = _StubAgentClient(
        scripts=[
            [TextDelta(text="One-shot answer."), End(finish_reason="stop")],
        ]
    )
    _patch_client(monkeypatch, stub)

    out = await toolbox._tool_consult_specialist(
        {
            "specialist": "designer",
            "task": "Quick UX check on the dashboard handoff button.",
        }
    )
    payload = json.loads(out)
    assert payload["specialist"] == "designer"
    assert payload["report"] == "One-shot answer."
    assert payload["tool_calls_used"] == 0
    assert payload["finish_reason"] == "stop"
    assert "error" not in payload

    # Recursion guard: tool list passed to the stub MUST NOT include
    # ``specialist_consult`` — otherwise the subagent could self-call.
    assert "specialist_consult" not in stub.calls[0]["tool_names"]
    # Subagent's system prompt carries the role file's name + the
    # subagent framing block. We don't assert on copy, only structure.
    sys_msg = stub.calls[0]["messages"][0]
    assert sys_msg["role"] == "system"
    assert "subagent" in sys_msg["content"].lower()
    user_msg = stub.calls[0]["messages"][1]
    assert user_msg["role"] == "user"
    assert "Quick UX check" in user_msg["content"]


@pytest.mark.asyncio
async def test_subagent_runs_one_tool_then_finalises(
    toolbox, monkeypatch
) -> None:
    """Turn 1: model emits a tool call. Loop runs the tool, appends
    the result, calls astream again. Turn 2: model produces text + no
    tool calls. Handler returns the text and ``tool_calls_used=1``."""
    from backend.app.services.agent.client import End, TextDelta, ToolCall

    stub = _StubAgentClient(
        scripts=[
            # Turn 1: ask for activated repos.
            [
                ToolCall(
                    id="call-1", name="list_activated_repos", arguments={}
                ),
                End(finish_reason="tool_use"),
            ],
            # Turn 2: model has the tool result, produces final text.
            [
                TextDelta(text="Found one repo. Done."),
                End(finish_reason="stop"),
            ],
        ]
    )
    _patch_client(monkeypatch, stub)

    out = await toolbox._tool_consult_specialist(
        {"specialist": "tech-architect", "task": "List the repos."}
    )
    payload = json.loads(out)
    assert payload["report"] == "Found one repo. Done."
    assert payload["tool_calls_used"] == 1
    assert payload["finish_reason"] == "stop"

    # Stub got called exactly twice and the second call has the tool
    # result in its message stack.
    assert len(stub.calls) == 2
    second_messages = stub.calls[1]["messages"]
    roles = [m["role"] for m in second_messages]
    assert "tool" in roles  # the tool result re-injected for turn 2


@pytest.mark.asyncio
async def test_subagent_bails_at_tool_call_cap(
    toolbox, monkeypatch
) -> None:
    """A runaway specialist that keeps emitting tool calls hits the
    25-call cap and the handler returns ``finish_reason=tool_loop_exceeded``
    rather than spinning forever."""
    from backend.app.services.agent.client import End, ToolCall

    # Script: every turn the model emits one tool call (forever).
    looping_turn = [
        ToolCall(id="call-x", name="list_activated_repos", arguments={}),
        End(finish_reason="tool_use"),
    ]
    stub = _StubAgentClient(scripts=[looping_turn] * 50)  # plenty
    _patch_client(monkeypatch, stub)
    # Tighten the cap so the test runs fast — same code path.
    monkeypatch.setattr(toolbox, "_SUBAGENT_MAX_TOOL_CALLS", 3)

    out = await toolbox._tool_consult_specialist(
        {"specialist": "ba", "task": "Stress the loop cap."}
    )
    payload = json.loads(out)
    assert payload["finish_reason"] == "tool_loop_exceeded"
    assert payload["tool_calls_used"] == 3
    assert "error" in payload
    assert "tool-call cap" in payload["error"]


@pytest.mark.asyncio
async def test_subagent_surfaces_agent_unavailable(
    toolbox, monkeypatch
) -> None:
    """When ``pick_default_client`` raises (no LLM key), the handler
    must NOT raise to the parent — instead it returns a structured
    ``finish_reason=agent_unavailable`` so the LLM can phrase a useful
    response to the user."""
    def _broken(*_args, **_kwargs):
        raise RuntimeError("no LLM key configured")

    monkeypatch.setattr(
        "backend.app.services.agent.client.pick_default_client", _broken
    )

    out = await toolbox._tool_consult_specialist(
        {"specialist": "designer", "task": "Anything."}
    )
    payload = json.loads(out)
    assert payload["finish_reason"] == "agent_unavailable"
    assert payload["report"] == ""
    assert "no LLM key" in payload["error"]


@pytest.mark.asyncio
async def test_audit_log_row_inserted_on_success(
    toolbox, monkeypatch, db_session, seed_workspace
) -> None:
    """A subagent run must drop an audit row so an operator can
    answer 'when did the navigator delegate to a designer?' from the
    audit page without scraping LLM logs."""
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.agent.client import End, TextDelta

    _, _, workspace = seed_workspace

    stub = _StubAgentClient(
        scripts=[[TextDelta(text="Ok."), End(finish_reason="stop")]]
    )
    _patch_client(monkeypatch, stub)

    await toolbox._tool_consult_specialist(
        {"specialist": "qa-architect", "task": "Any test gaps in the dashboard?"}
    )

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.specialist_consult",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_id == "qa-architect"
    assert rows[0].payload["specialist"] == "qa-architect"
    assert rows[0].payload["had_error"] is False
