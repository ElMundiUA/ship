"""E16 / ELS-121 — unit + integration tests for tracker_poller.

Three correctness axes covered:

1. Diff math — first-poll emits one event per ticket with
   ``old_state=None``; subsequent polls emit only on actual state
   change.
2. Cursor advance — ``cursor.updated_at`` moves forward across ticks
   (with ``CURSOR_OVERLAP_S`` lookback to absorb clock skew), and
   ``cursor.states`` accumulates one entry per seen ticket.
3. No duplicate audit rows — re-running the poller with no upstream
   change writes zero new ``tracker.event.received`` rows.

HTTP boundary is monkey-patched at ``_fetch_updated_issues`` so tests
never actually hit Linear.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationSyncState,
)
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.security.encryption import encrypt
from backend.app.services import tracker_poller


@pytest.fixture
def _patch_sessionmaker(db_session, monkeypatch):
    """Wire the poller's session opener back into the test's transactional
    session. Without this the poller's ``async with sm() as session``
    opens a fresh connection that doesn't see anything the test wrote.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _bound_session_factory():
        # Yield the test's own session; the poller will treat
        # ``await session.commit()`` as a savepoint commit because
        # db_session is inside an outer transaction (rolled back at
        # teardown). Effectively idempotent for the assertions.
        yield db_session

    class _SM:
        def __call__(self):
            return _bound_session_factory()

    monkeypatch.setattr(tracker_poller, "get_sessionmaker", lambda: _SM())


# Fake Linear issue payload that mirrors what _fetch_updated_issues
# returns. Tests pick which subset to ship per tick.
def _issue(identifier: str, state: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "identifier": identifier,
        "title": f"Test ticket {identifier}",
        "state": {"name": state},
        "updatedAt": updated_at,
    }


async def _seed_linear_install(db_session, workspace_id) -> uuid.UUID:
    """Insert a NativeIntegrationInstallation + access_token credential
    + legacy Integration row carrying ``team_id`` so the poller has
    something to claim.
    """
    install = NativeIntegrationInstallation(
        workspace_id=workspace_id,
        provider="linear",
        auth_mode="oauth",
        external_account_id="default",
        external_account_name="Linear workspace",
        capabilities={},
        scopes=[],
        config={},
        status="ready",
    )
    db_session.add(install)
    await db_session.flush()

    db_session.add(
        NativeIntegrationCredential(
            installation_id=install.id,
            kind="access_token",
            # encrypt() outputs a Fernet ciphertext; safe_decrypt
            # round-trips it back to the plaintext token string.
            secret_ciphertext=encrypt("lin_oauth_test_token_xxx"),
            scopes=[],
        )
    )
    db_session.add(
        Integration(
            workspace_id=workspace_id,
            kind="linear",
            config={"team_id": "test-team-uuid"},
            status="ok",
        )
    )
    await db_session.flush()
    return install.id


@pytest.mark.asyncio
async def test_first_poll_seeds_audit_rows_with_old_state_none(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """On the very first tick every ticket is "new to us" — emit one
    audit row each with ``old_state=None``."""
    _, _, workspace = seed_workspace
    install_id = await _seed_linear_install(db_session, workspace.id)
    await db_session.commit()

    issues = [
        _issue("TST-1", "Backlog", "2026-05-14T00:00:00.000Z"),
        _issue("TST-2", "Todo", "2026-05-14T00:00:01.000Z"),
        _issue("TST-3", "In Progress", "2026-05-14T00:00:02.000Z"),
    ]

    async def _fake_fetch(**_kw):
        return issues

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    summary = await tracker_poller.poll_once()
    assert summary["events"] == 3
    assert summary["issues"] == 3
    assert summary["errors"] == 0

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace.id,
                    AuditLog.action == "tracker.event.received",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3
    by_ref = {r.target_id: r for r in rows}
    assert by_ref["TST-1"].payload["old_state"] is None
    assert by_ref["TST-1"].payload["new_state"] == "Backlog"
    assert by_ref["TST-3"].payload["new_state"] == "In Progress"


@pytest.mark.asyncio
async def test_second_poll_with_no_changes_emits_zero(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """Idempotency — same upstream snapshot twice must not double-emit."""
    _, _, workspace = seed_workspace
    await _seed_linear_install(db_session, workspace.id)
    await db_session.commit()

    issues = [_issue("TST-1", "Backlog", "2026-05-14T00:00:00.000Z")]

    async def _fake_fetch(**_kw):
        return issues

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    s1 = await tracker_poller.poll_once()
    s2 = await tracker_poller.poll_once()

    assert s1["events"] == 1
    assert s2["events"] == 0


@pytest.mark.asyncio
async def test_state_change_emits_single_event(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """Move a ticket from Backlog → In Progress between ticks; expect
    exactly one new audit row with old_state=Backlog."""
    _, _, workspace = seed_workspace
    await _seed_linear_install(db_session, workspace.id)
    await db_session.commit()

    state = {"current": "Backlog"}

    async def _fake_fetch(**_kw):
        return [_issue("TST-1", state["current"], "2026-05-14T00:00:00.000Z")]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    await tracker_poller.poll_once()
    state["current"] = "In Progress"
    summary = await tracker_poller.poll_once()
    assert summary["events"] == 1

    latest = (
        await db_session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "tracker.event.received",
                AuditLog.target_id == "TST-1",
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).scalar_one()
    assert latest.payload["old_state"] == "Backlog"
    assert latest.payload["new_state"] == "In Progress"


@pytest.mark.asyncio
async def test_cursor_advances_with_overlap(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """Cursor's ``updated_at`` is set to ``max(updatedAt) - overlap``;
    states map carries every seen ticket."""
    _, _, workspace = seed_workspace
    install_id = await _seed_linear_install(db_session, workspace.id)
    await db_session.commit()

    async def _fake_fetch(**_kw):
        return [
            _issue("TST-1", "Backlog", "2026-05-14T00:00:00.000Z"),
            _issue("TST-2", "Todo", "2026-05-14T00:05:00.000Z"),
        ]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    await tracker_poller.poll_once()

    cursor_row = (
        await db_session.execute(
            select(NativeIntegrationSyncState).where(
                NativeIntegrationSyncState.installation_id == install_id,
                NativeIntegrationSyncState.sync_kind == "tracker_poll",
            )
        )
    ).scalar_one()
    assert cursor_row.status == "ready"
    # max(updatedAt) = 2026-05-14T00:05:00; overlap pads 60s back ⇒
    # cursor must be earlier than max but later than min.
    saved = cursor_row.cursor["updated_at"]
    assert "2026-05-14" in saved
    assert saved < "2026-05-14T00:05:00"
    assert saved > "2026-05-14T00:00:00"
    assert set(cursor_row.cursor["states"].keys()) == {"TST-1", "TST-2"}


@pytest.mark.asyncio
async def test_install_without_team_id_is_skipped(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """Installations missing ``team_id`` in both native and legacy
    config are logged + skipped, not crashed."""
    _, _, workspace = seed_workspace
    install = NativeIntegrationInstallation(
        workspace_id=workspace.id,
        provider="linear",
        auth_mode="oauth",
        external_account_id="default",
        external_account_name="Linear workspace",
        capabilities={},
        scopes=[],
        config={},
        status="ready",
    )
    db_session.add(install)
    await db_session.flush()
    db_session.add(
        NativeIntegrationCredential(
            installation_id=install.id,
            kind="access_token",
            secret_ciphertext=encrypt("lin_oauth_test"),
            scopes=[],
        )
    )
    await db_session.commit()

    async def _fake_fetch(**_kw):
        raise AssertionError(
            "_fetch_updated_issues should not be called when team_id is missing"
        )

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    summary = await tracker_poller.poll_once()
    # The skip happens inside _poll_installation; the loop continues
    # and reports 0 errors. The installation IS counted in ``installs``.
    assert summary["errors"] == 0
    assert summary["events"] == 0


# ---------------------------------------------------------------------------
# ELS-250 — agent-vs-human via author identity (marker = legacy fallback)
# ---------------------------------------------------------------------------


def test_human_pasting_marker_is_human() -> None:
    """AC: a comment whose body QUOTES the marker but authored by a
    human classifies as HUMAN."""
    from backend.app.services.tracker_poller import _is_agent_comment

    comment = {
        "body": "Replying inline:\n> blah [Ship SDLC:role-developer]\nyes, ship it",
        "user": {"displayName": "Denys", "email": "denys@bodyman.io", "isMe": False},
    }
    assert _is_agent_comment(comment) is False


def test_service_account_comment_is_agent_without_marker() -> None:
    """AC: authored by the workspace identity → AGENT even when the
    marker text is absent."""
    from backend.app.services.tracker_poller import _is_agent_comment

    comment = {
        "body": "Working on it.",
        "user": {"displayName": "Ship Agent", "email": "agent@ship", "isMe": True},
    }
    assert _is_agent_comment(comment) is True


def test_bot_actor_comment_is_agent() -> None:
    from backend.app.services.tracker_poller import _is_agent_comment

    comment = {"body": "Done.", "user": None, "botActor": {"id": "app-1"}}
    assert _is_agent_comment(comment) is True


def test_legacy_comment_without_author_falls_back_to_marker() -> None:
    """AC: no author identity → marker regex keeps classifying
    historical rows."""
    from backend.app.services.tracker_poller import _is_agent_comment

    agent_legacy = {"body": "Asked a question. [Ship SDLC:role-ba]"}
    human_legacy = {"body": "here's the answer"}
    assert _is_agent_comment(agent_legacy) is True
    assert _is_agent_comment(human_legacy) is False


def test_clarification_walk_uses_identity() -> None:
    """Newest-first walk: human answer above the agent question →
    answered; agent question on top → not answered. Identity decides
    even when the human quotes the marker."""
    from backend.app.services.tracker_poller import (
        _operator_answered_clarification,
    )

    agent_q = {
        "body": "Which region? [Ship SDLC:role-ba]",
        "createdAt": "2026-06-12T10:00:00Z",
        "user": {"displayName": "Ship", "isMe": True},
    }
    human_quoting = {
        "body": "> [Ship SDLC:role-ba] Which region?\neu-central please",
        "createdAt": "2026-06-12T11:00:00Z",
        "user": {"displayName": "Denys", "isMe": False},
    }
    assert _operator_answered_clarification([agent_q, human_quoting]) is True
    assert _operator_answered_clarification([agent_q]) is False
