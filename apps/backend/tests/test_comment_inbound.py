"""ELS-251 — Linear operator comments → Navigator turn (context-only).

Pins the four contract points:

- OFF by default: the poller never calls the ingest service unless the
  workspace opted into ``chat.comment_inbound``.
- Baseline-then-ingest: first sight of a ticket baselines its newest
  comment without ingesting (no history replay); a comment newer than
  the baseline triggers exactly one Navigator turn.
- Idempotency: re-polling with no new comment runs zero turns (the
  comment cursor rides the poller cursor JSONB).
- Context-only: ingestion writes NO ``tracker.event.received`` rows —
  the FSM sees nothing.

The ingest service itself is unit-tested with the turn generator
stubbed: user message persisted on the ticket thread with
source-metadata, turn driven with classify_shift=False semantics
(the service hard-codes it).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_surface import ChatMessage, ChatThread
from backend.app.db.models.tenancy import AuditLog
from backend.app.services import tracker_poller
from backend.app.services.agent import comment_inbound

from backend.tests.test_tracker_poller import (  # noqa: F401 — reuse harness
    _patch_sessionmaker,
    _seed_linear_install,
)


def _issue_with_comments(
    identifier: str,
    state: str,
    updated_at: str,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "identifier": identifier,
        "title": f"Ticket {identifier}",
        "state": {"name": state},
        "updatedAt": updated_at,
        "comments": {"nodes": comments},
    }


def _human(body: str, created_at: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "body": body,
        "createdAt": created_at,
        "user": {"displayName": "Denys", "email": "d@x", "isMe": False},
    }


def _agent(body: str, created_at: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "body": body,
        "createdAt": created_at,
        "user": {"displayName": "Ship", "email": "a@x", "isMe": True},
    }


async def _enable_flag(db_session, workspace) -> None:
    workspace.settings = {
        **(workspace.settings or {}),
        "chat": {"comment_inbound": True},
    }
    await db_session.flush()


@pytest.fixture
def ingest_spy(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(comment_inbound, "ingest_operator_comment", spy)
    return spy


@pytest.mark.asyncio
async def test_flag_off_by_default_never_ingests(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker, ingest_spy
) -> None:
    _, _, workspace = seed_workspace
    await _seed_linear_install(db_session, workspace.id)
    await db_session.commit()

    issues = [
        _issue_with_comments(
            "TST-1", "In Progress", "2026-06-12T10:00:00.000Z",
            [_human("ship it", "2026-06-12T09:00:00.000Z")],
        )
    ]

    async def _fake_fetch(**_kw):
        return issues

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    await tracker_poller.poll_once()
    await tracker_poller.poll_once()
    ingest_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_baseline_then_single_turn_then_idempotent(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker, ingest_spy
) -> None:
    _, _, workspace = seed_workspace
    await _seed_linear_install(db_session, workspace.id)
    await _enable_flag(db_session, workspace)
    await db_session.commit()

    old_comment = _agent("Asked something. [Ship SDLC:role-ba]", "2026-06-12T08:00:00.000Z")
    tick: dict[str, Any] = {
        "issues": [
            _issue_with_comments(
                "TST-1", "In Progress", "2026-06-12T08:30:00.000Z", [old_comment]
            )
        ]
    }

    async def _fake_fetch(**_kw):
        return tick["issues"]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    # Tick 1 — first sight baselines, no ingestion (no history replay).
    await tracker_poller.poll_once()
    ingest_spy.assert_not_awaited()

    # Tick 2 — fresh human comment newer than the baseline → exactly
    # one turn, carrying the comment body.
    fresh = _human("please use eu-central", "2026-06-12T09:00:00.000Z")
    tick["issues"] = [
        _issue_with_comments(
            "TST-1", "In Progress", "2026-06-12T09:00:01.000Z",
            [old_comment, fresh],
        )
    ]
    audit_before = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "tracker.event.received",
            )
        )
    ).scalars().all()

    await tracker_poller.poll_once()
    assert ingest_spy.await_count == 1
    kwargs = ingest_spy.await_args.kwargs
    assert kwargs["ticket_ref"] == "TST-1"
    assert kwargs["comment_body"] == "please use eu-central"
    assert kwargs["comment_author"] == "Denys"

    # Context-only: comment ingestion produced NO tracker events —
    # the FSM never hears about comments.
    audit_after = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "tracker.event.received",
            )
        )
    ).scalars().all()
    assert len(audit_after) == len(audit_before)

    # Tick 3 — same payload again → zero additional turns.
    await tracker_poller.poll_once()
    assert ingest_spy.await_count == 1


@pytest.mark.asyncio
async def test_agent_comments_never_ingested(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker, ingest_spy
) -> None:
    """The agent must never reply to itself: a fresh comment authored
    by the workspace identity is cursor-advanced but not ingested."""
    _, _, workspace = seed_workspace
    await _seed_linear_install(db_session, workspace.id)
    await _enable_flag(db_session, workspace)
    await db_session.commit()

    seed_comment = _human("kickoff note", "2026-06-12T08:00:00.000Z")
    tick: dict[str, Any] = {
        "issues": [
            _issue_with_comments(
                "TST-2", "In Progress", "2026-06-12T08:30:00.000Z", [seed_comment]
            )
        ]
    }

    async def _fake_fetch(**_kw):
        return tick["issues"]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)
    await tracker_poller.poll_once()  # baseline

    tick["issues"] = [
        _issue_with_comments(
            "TST-2", "In Progress", "2026-06-12T09:00:01.000Z",
            [seed_comment, _agent("On it. [Ship SDLC:role-developer]", "2026-06-12T09:00:00.000Z")],
        )
    ]
    await tracker_poller.poll_once()
    ingest_spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# Service-level: thread + message persistence with the turn stubbed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_persists_user_message_and_runs_turn(
    db_session, seed_workspace, monkeypatch
) -> None:
    user, _, workspace = seed_workspace

    async def _fake_turn(**kwargs):
        # The service must hard-code classify_shift=False (context
        # inbound is never a topic/drafting trigger).
        assert kwargs["classify_shift"] is False
        yield b"data: {}\n\n"

    monkeypatch.setattr(
        "backend.app.api.v1.routes.chat._run_agent_turn", _fake_turn
    )
    monkeypatch.setattr(
        "backend.app.services.agent.client.pick_default_client",
        lambda _s: object(),
    )

    from backend.app.core.config import Settings

    ran = await comment_inbound.ingest_operator_comment(
        db_session,
        settings=Settings(OPENAI_API_KEY="test"),  # type: ignore[call-arg]
        workspace_id=workspace.id,
        ticket_ref="TST-9",
        ticket_title="Demo ticket",
        comment_id="c-1",
        comment_body="use the staging bucket",
        comment_author="Denys",
    )
    assert ran is True

    thread = (
        await db_session.execute(
            select(ChatThread).where(
                ChatThread.workspace_id == workspace.id,
                ChatThread.resolved_ticket_ref == "TST-9",
            )
        )
    ).scalar_one()
    assert thread.status == "active"
    assert thread.created_by_user_id == user.id

    msg = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id)
        )
    ).scalar_one()
    assert msg.role == "user"
    assert "use the staging bucket" in msg.body
    assert msg.meta["source"] == "linear_comment"
    assert msg.meta["comment_id"] == "c-1"


@pytest.mark.asyncio
async def test_ingest_skips_cleanly_without_llm(
    db_session, seed_workspace, monkeypatch
) -> None:
    def _raise(_s):
        raise RuntimeError("no key")

    monkeypatch.setattr(
        "backend.app.services.agent.client.pick_default_client", _raise
    )

    from backend.app.core.config import Settings

    ran = await comment_inbound.ingest_operator_comment(
        db_session,
        settings=Settings(OPENAI_API_KEY="test"),  # type: ignore[call-arg]
        workspace_id=seed_workspace[2].id,
        ticket_ref="TST-9",
        ticket_title=None,
        comment_id="c-2",
        comment_body="hello",
        comment_author=None,
    )
    assert ran is False
