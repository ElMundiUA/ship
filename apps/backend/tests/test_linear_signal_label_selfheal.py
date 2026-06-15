"""ELS-321 — add_signal_label self-heals a missing signal label instead
of raising (which made agent_run.finish mislabel a hard `blocked` as
`needs:clarification` — a phantom clarification that froze the ticket
until an operator hand-removed the label)."""

from __future__ import annotations

import pytest

from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.integrations.linear.tracker_adapter import LinearTracker


def _ref() -> TicketRef:
    return TicketRef(kind="linear", workspace_hint=None, id="BUZ-11")


def _tracker_without_blocked() -> tuple[LinearTracker, list[dict]]:
    """Tracker whose team has `needs_clarification` but NOT `blocked`."""
    t = LinearTracker(
        "tok",
        team_id="team-1",
        signal_label_ids={"needs_clarification": "L-clar"},
    )
    calls: list[dict] = []

    async def fake_gql(query: str, variables: dict):  # type: ignore[override]
        calls.append({"query": query, "variables": variables})
        if "issueLabels(" in query:
            # Default: label does NOT exist on the team yet.
            return {"issueLabels": {"nodes": []}}
        if "issueLabelCreate" in query:
            return {"issueLabelCreate": {"success": True, "issueLabel": {"id": "L-blocked"}}}
        if "issueUpdate" in query:
            return {"issueUpdate": {"success": True}}
        return {}

    t._gql = fake_gql  # type: ignore[assignment]
    return t, calls


@pytest.mark.asyncio
async def test_add_signal_label_creates_missing_blocked_label() -> None:
    t, calls = _tracker_without_blocked()

    await t.add_signal_label(_ref(), key="blocked")

    # It looked the label up, created it, cached the id, and applied it.
    assert any("issueLabels(" in c["query"] for c in calls)
    assert any("issueLabelCreate" in c["query"] for c in calls)
    assert t._signal_label_ids["blocked"] == "L-blocked"
    applied = [c for c in calls if "issueUpdate" in c["query"]]
    assert applied, "expected the label to be applied via issueUpdate"
    assert applied[0]["variables"]["input"]["addedLabelIds"] == ["L-blocked"]
    # Never borrowed the clarification label.
    assert t._signal_label_ids["needs_clarification"] == "L-clar"


@pytest.mark.asyncio
async def test_add_signal_label_reuses_existing_label_no_create() -> None:
    t, calls = _tracker_without_blocked()

    async def fake_gql(query: str, variables: dict):
        calls.append({"query": query, "variables": variables})
        if "issueLabels(" in query:
            return {"issueLabels": {"nodes": [{"id": "L-existing", "name": "blocked"}]}}
        if "issueUpdate" in query:
            return {"issueUpdate": {"success": True}}
        if "issueLabelCreate" in query:  # pragma: no cover - must not happen
            raise AssertionError("should not create when label already exists")
        return {}

    t._gql = fake_gql  # type: ignore[assignment]

    await t.add_signal_label(_ref(), key="blocked")

    assert t._signal_label_ids["blocked"] == "L-existing"
    assert not any("issueLabelCreate" in c["query"] for c in calls)


@pytest.mark.asyncio
async def test_add_signal_label_existing_key_skips_provisioning() -> None:
    """A team that already has the label id cached never lists/creates."""
    t = LinearTracker(
        "tok", team_id="team-1", signal_label_ids={"blocked": "L-blocked"}
    )
    calls: list[dict] = []

    async def fake_gql(query: str, variables: dict):
        calls.append(query)
        return {"issueUpdate": {"success": True}}

    t._gql = fake_gql  # type: ignore[assignment]
    await t.add_signal_label(_ref(), key="blocked")
    assert not any("issueLabels(" in q or "issueLabelCreate" in q for q in calls)
