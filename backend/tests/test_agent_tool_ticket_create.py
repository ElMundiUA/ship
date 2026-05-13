"""Handler-level tests for ``ticket_create`` ``type`` validation (ELS-69).

The tool-layer enum gate is the only line of defence before unbounded
vendor calls. We parametrise widely because the LLM may send malformed
inputs that JSON-schema alone wouldn't catch — string casings, ints,
empty arrays, the literal string ``"None"``, etc.

We stub the tracker entirely so the test is fast and doesn't depend on
DB / network. Each parametrised bad-input asserts both
``ToolInvocationError`` AND that the stub's ``create_ticket`` was never
called — proving the gate fires *before* any tracker round-trip.
"""

from __future__ import annotations

import json

import pytest

from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef
from backend.app.services.agent.tools import ToolBox, ToolInvocationError


class _StubTracker:
    """Records every ``create_ticket`` invocation; returns a canned
    success so the handler completes its happy path."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_ticket(self, **kwargs):
        self.calls.append(kwargs)
        return CreatedTicket(
            ref=TicketRef(
                kind="linear",
                workspace_hint="team-uuid",
                id="issue-uuid",
            ),
            url="https://linear.app/x/issue/ELS-1",
            display_id="ELS-1",
        )


def _toolbox_with_stub(stub: _StubTracker) -> ToolBox:
    box = ToolBox(
        session=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        workspace_id=None,  # type: ignore[arg-type]
        user_id=None,  # type: ignore[arg-type]
    )

    async def _resolve(_kind, _hint):
        return stub

    box._resolve_tracker = _resolve  # type: ignore[assignment]
    return box


@pytest.mark.parametrize(
    "bad_type",
    ["bugs", "BUG", "Bug", 42, "", [], "None", "feature ", " task"],
)
@pytest.mark.asyncio
async def test_type_enum_rejects_unknown_values(bad_type) -> None:
    """Any value outside the canonical lowercase enum raises
    ``ToolInvocationError`` BEFORE any tracker call. The list covers
    casing, types, whitespace, and adjacent-but-wrong tokens the LLM
    might emit when it half-remembers the spec."""
    stub = _StubTracker()
    box = _toolbox_with_stub(stub)

    with pytest.raises(ToolInvocationError, match="type must be one of"):
        await box._tool_ticket_create(
            {"title": "t", "body": "b", "type": bad_type}
        )
    assert stub.calls == [], (
        "tracker was reached despite the validation gate firing — "
        "the enum check must happen before _resolve_tracker"
    )


@pytest.mark.asyncio
async def test_default_path_forwards_ticket_type_none() -> None:
    """AC #2 no-drift gate at the handler layer: omitting ``type``
    forwards ``ticket_type=None`` so adapters preserve today's
    behaviour."""
    stub = _StubTracker()
    box = _toolbox_with_stub(stub)

    raw = await box._tool_ticket_create({"title": "t", "body": "b"})
    payload = json.loads(raw)
    assert payload["display_id"] == "ELS-1"
    assert len(stub.calls) == 1
    assert stub.calls[0]["ticket_type"] is None


@pytest.mark.parametrize("good_type", ["bug", "feature", "task"])
@pytest.mark.asyncio
async def test_handler_forwards_each_canonical_value(good_type) -> None:
    """Each canonical enum value threads through to the tracker
    kwarg verbatim — no implicit downcasing surprises, no rewrites."""
    stub = _StubTracker()
    box = _toolbox_with_stub(stub)

    await box._tool_ticket_create(
        {"title": "t", "body": "b", "type": good_type}
    )
    assert stub.calls[0]["ticket_type"] == good_type
