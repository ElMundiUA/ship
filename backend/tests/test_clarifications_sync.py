"""Tracker → Ship clarifications projection (D13).

Covers:

- Marker parsers: ``@ship clarification:`` + ``@ship answer:``,
  blockquote-tolerant, case-insensitive, dominant first match.
- :func:`sync_workspace` ingests labelled issues with fake gateway.
- Re-running the sync is idempotent (no duplicate rows).
- Removing the label marks the ``open`` Ship row ``stale``.
- An ``@ship answer:`` comment in the tracker auto-closes the open
  row with ``status='answered'``.
- PATCH write-back: answering a ``source='tracker'`` row posts the
  answer comment + strips the label via the bound tracker.
- PATCH write-back on a manual row stays Ship-local (no tracker call).
- Tracker write-back failure → 502, row unchanged.

The tests use an in-process fake gateway that implements the subset
of :class:`TrackerGateway` the projection calls. We do not exercise
real Linear / GitHub adapters here; their extension is covered by
their own smoke tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_surface import Clarification
from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)
from backend.app.services import clarifications_sync
from backend.app.services.clarifications_sync import (
    CLARIFICATION_LABEL,
    TrackerBinding,
    parse_answer_body,
    parse_clarification_body,
    render_answer_comment,
    sync_workspace,
)


# ---------------------------------------------------------------------------
# Fake gateway
# ---------------------------------------------------------------------------


@dataclass
class _FakeIssue:
    ref: TicketRef
    display_id: str
    url: str | None
    label: str = CLARIFICATION_LABEL
    comments: list[CommentRef] = field(default_factory=list)


class _FakeTracker:
    """Drop-in :class:`TrackerGateway` for projection tests."""

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._issues: dict[str, _FakeIssue] = {}
        self.comments_posted: list[tuple[str, str]] = []
        self.labels_removed: list[tuple[str, str]] = []

    # Test helpers ----------------------------------------------------

    def add_issue(self, issue: _FakeIssue) -> None:
        self._issues[issue.ref.id] = issue

    def strip_label(self, issue_id: str) -> None:
        issue = self._issues.get(issue_id)
        if issue:
            issue.label = ""

    # Protocol surface ------------------------------------------------

    async def list_tickets(self, *, limit: int = 10) -> list[dict[str, Any]]:
        raise AssertionError("projection must not call list_tickets")

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        raise AssertionError("projection must not transition tickets")

    async def create_ticket(self, **_kw) -> CreatedTicket:
        raise AssertionError("projection must not create tickets")

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        self.comments_posted.append((ticket.id, body))

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        out: list[ListedIssue] = []
        for issue in self._issues.values():
            if issue.label == label:
                out.append(
                    ListedIssue(
                        ref=issue.ref, display_id=issue.display_id, url=issue.url
                    )
                )
        return out

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        issue = self._issues.get(ticket.id)
        if issue is None:
            return []
        return list(issue.comments)

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        self.labels_removed.append((ticket.id, label))
        issue = self._issues.get(ticket.id)
        if issue and issue.label == label:
            issue.label = ""


# ---------------------------------------------------------------------------
# Marker parsers (pure / cheap)
# ---------------------------------------------------------------------------


class TestMarkerParsers:
    def test_plain_question(self) -> None:
        body = "@ship clarification: Which queue should the worker consume?"
        assert (
            parse_clarification_body(body)
            == "Which queue should the worker consume?"
        )

    def test_blockquote_markdown_wrapper(self) -> None:
        body = (
            "> **@ship clarification:**\n"
            "> Which queue should the worker consume?\n"
            "> Any of these would work: q1, q2.\n"
        )
        parsed = parse_clarification_body(body)
        assert parsed == (
            "Which queue should the worker consume?\n"
            "Any of these would work: q1, q2."
        )

    def test_case_insensitive(self) -> None:
        assert parse_clarification_body("@SHIP Clarification: hi") == "hi"

    def test_answer_stops_question_capture(self) -> None:
        body = (
            "@ship clarification: pick one?\n"
            "options: a, b\n"
            "\n"
            "@ship answer: b please"
        )
        assert parse_clarification_body(body) == "pick one?\noptions: a, b"
        assert parse_answer_body(body) == "b please"

    def test_no_marker_returns_none(self) -> None:
        assert parse_clarification_body("plain comment, no marker") is None
        assert parse_answer_body("plain comment, no marker") is None

    def test_render_answer_comment_round_trips(self) -> None:
        composed = render_answer_comment("ship it, but add retries=3")
        assert parse_answer_body(composed) == "ship it, but add retries=3"


# ---------------------------------------------------------------------------
# Projection: ingest / idempotency / stale / answer-by-tracker
# ---------------------------------------------------------------------------


def _ticket(provider: str, vendor_id: str, hint: str | None = None) -> TicketRef:
    return TicketRef(
        kind=provider if provider in {"linear", "github_issues", "notion"}
        else "linear",  # type: ignore[arg-type]
        workspace_hint=hint,
        id=vendor_id,
    )


def _comment(
    cid: str,
    body: str,
    *,
    author: str = "agent",
    created: datetime | None = None,
) -> CommentRef:
    return CommentRef(
        id=cid,
        body=body,
        author=author,
        created_at=created or datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        url=f"https://example.test/c/{cid}",
    )


@pytest_asyncio.fixture
async def tracker_workspace(db_session, seed_workspace):
    """Workspace with no integrations — tests inject bindings manually."""
    _, _, workspace = seed_workspace
    return workspace


@pytest.mark.asyncio
async def test_sync_ingests_labelled_issue_with_question(
    db_session, tracker_workspace, monkeypatch
) -> None:
    workspace = tracker_workspace
    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-1", hint="team-A"),
            display_id="ENG-42",
            url="https://linear.app/acme/issue/ENG-42",
            comments=[
                _comment("c1", "Scoping the feature..."),
                _comment(
                    "c2",
                    "> **@ship clarification:**\n> Retry policy for intake?",
                ),
            ],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    report = await sync_workspace(
        db_session,
        settings=None,  # type: ignore[arg-type]  # bindings supplied, resolver skipped
        workspace_id=workspace.id,
        bindings=[binding],
    )
    await db_session.flush()

    assert report.ingested == 1
    assert report.stale_marked == 0
    rows = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "tracker"
    assert row.tracker_provider == "linear"
    assert row.tracker_comment_id == "c2"
    assert row.tracker_issue_key == "ENG-42"
    assert row.status == "open"
    assert row.question == "Retry policy for intake?"


@pytest.mark.asyncio
async def test_sync_is_idempotent(db_session, tracker_workspace) -> None:
    workspace = tracker_workspace
    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-idem"),
            display_id="ENG-7",
            url=None,
            comments=[_comment("cX", "@ship clarification: ping?")],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    r1 = await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()
    r2 = await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()

    assert r1.ingested == 1
    assert r2.ingested == 0
    rows = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_sync_marks_row_stale_when_label_removed(
    db_session, tracker_workspace
) -> None:
    workspace = tracker_workspace
    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-stale"),
            display_id="ENG-99",
            url=None,
            comments=[_comment("cS", "@ship clarification: temporary?")],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()
    tracker.strip_label("uuid-stale")
    report = await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()

    assert report.stale_marked == 1
    row = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().one()
    assert row.status == "stale"


@pytest.mark.asyncio
async def test_tracker_answer_closes_open_row(
    db_session, tracker_workspace
) -> None:
    workspace = tracker_workspace
    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-ans"),
            display_id="ENG-15",
            url=None,
            comments=[
                _comment(
                    "cq",
                    "@ship clarification: retries?",
                    created=datetime(2026, 4, 1, 10, tzinfo=timezone.utc),
                ),
                _comment(
                    "ca",
                    "@ship answer: 3 attempts with expo backoff",
                    created=datetime(2026, 4, 1, 11, tzinfo=timezone.utc),
                ),
            ],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().one()
    assert row.status == "answered"
    assert row.answer == "3 attempts with expo backoff"
    assert row.answered_at is not None


# ---------------------------------------------------------------------------
# PATCH writeback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_writeback_for_tracker_row(
    db_session, tracker_workspace, monkeypatch, v1_client, seed_workspace
) -> None:
    workspace = tracker_workspace
    _, raw, _ = seed_workspace

    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-wb", hint="team-X"),
            display_id="ENG-8",
            url=None,
            comments=[_comment("cc", "@ship clarification: A or B?")],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    # Seed the row via the projection (real code path).
    await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().one()

    # Monkey-patch the write-back binding resolver to return our fake.
    async def _resolver(*_a, **_kw):
        return [binding]

    monkeypatch.setattr(
        clarifications_sync, "resolve_tracker_bindings", _resolver
    )

    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{row.id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "pick A, we already depend on it"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "answered"
    assert body["source"] == "tracker"

    # Write-back side-effects: one comment, one label removal.
    assert len(tracker.comments_posted) == 1
    posted_ticket_id, posted_body = tracker.comments_posted[0]
    assert posted_ticket_id == "uuid-wb"
    assert "pick A" in posted_body
    assert parse_answer_body(posted_body) == "pick A, we already depend on it"
    assert tracker.labels_removed == [("uuid-wb", CLARIFICATION_LABEL)]


@pytest.mark.asyncio
async def test_patch_no_writeback_for_manual_row(
    db_session, seed_workspace, v1_client, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace

    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "manual q"},
        )
    ).json()

    called: list[bool] = []

    async def _resolver(*_a, **_kw):
        called.append(True)
        return []

    monkeypatch.setattr(
        clarifications_sync, "resolve_tracker_bindings", _resolver
    )

    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{created['id']}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "answered from the UI"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "manual"
    # Manual rows never trigger write-back; the resolver must not be
    # called even once.
    assert called == []


@pytest.mark.asyncio
async def test_patch_writeback_failure_returns_502(
    db_session, tracker_workspace, monkeypatch, v1_client, seed_workspace
) -> None:
    workspace = tracker_workspace
    _, raw, _ = seed_workspace

    class _BrokenTracker(_FakeTracker):
        async def comment(self, ticket, *, body):  # type: ignore[override]
            raise RuntimeError("linear upstream timeout")

    tracker = _BrokenTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-bad"),
            display_id="ENG-500",
            url=None,
            comments=[_comment("cbroken", "@ship clarification: will this fail?")],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    await sync_workspace(
        db_session, settings=None, workspace_id=workspace.id, bindings=[binding]  # type: ignore[arg-type]
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(Clarification).where(Clarification.workspace_id == workspace.id)
        )
    ).scalars().one()

    async def _resolver(*_a, **_kw):
        return [binding]

    monkeypatch.setattr(
        clarifications_sync, "resolve_tracker_bindings", _resolver
    )

    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/clarifications/{row.id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "yes it will"},
    )
    assert resp.status_code == 502, resp.text
    assert "Tracker write-back failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Admin-triggered sync endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_sync_endpoint_returns_report(
    db_session, seed_workspace, v1_client, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace

    tracker = _FakeTracker("linear")
    tracker.add_issue(
        _FakeIssue(
            ref=_ticket("linear", "uuid-admin"),
            display_id="ENG-7",
            url=None,
            comments=[_comment("c-admin", "@ship clarification: hi?")],
        )
    )
    binding = TrackerBinding(provider="linear", gateway=tracker)

    async def _resolver(*_a, **_kw):
        return [binding]

    monkeypatch.setattr(
        clarifications_sync, "resolve_tracker_bindings", _resolver
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/clarifications/sync",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ingested"] == 1
    assert body["stale_marked"] == 0
    assert body["workspace_id"] == str(workspace.id)
