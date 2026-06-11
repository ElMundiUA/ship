"""notify() seam — interface + the three channels (ELS-222).

Pins the Phase-1 gate: with the kill-switch off (default) every
emission is inbox-only and the InboxItem is field-identical with what
the intake builder produces today; channels swallow transport errors;
notify() never raises into the engine control path.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.db.models.inbox import InboxItem
from backend.app.services.notify import (
    NotifyLevel,
    notify,
)
from backend.app.services.notify_config import NotifyChannel


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(notification_channels_enabled=enabled)


async def _items_for(db_session, workspace_id):
    rows = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.workspace_id == workspace_id)
        )
    ).scalars().all()
    return rows


# ---------------------------------------------------------------------------
# Inbox channel — byte-identical with the intake builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item_type,level",
    [
        ("improvement", NotifyLevel.INFO),
        ("clarification", NotifyLevel.ACTION),
        ("blocker", NotifyLevel.BLOCKER),
    ],
)
async def test_inbox_item_field_identical_with_intake_builder(
    db_session, seed_workspace, item_type, level
) -> None:
    from backend.app.services.inbox.intake import (
        RunSummaryFinding,
        _build_inbox_item,
    )

    _, _, workspace = seed_workspace
    title = "Tracker unreachable — agents stalled" * 12  # force truncation path
    summary = "Re-authorize the workspace integration.\nSecond line." * 30
    payload = {"requires_approval": False, "ticket_ref": "ELS-1", "k": "v"}

    finding = RunSummaryFinding(
        type=item_type,
        title=title,
        summary=summary,
        payload={"ticket_ref": "ELS-1", "k": "v"},
        requires_approval=False,
    )
    resolved = SimpleNamespace(
        user_id=None, intake_handle="dedup:ELS-1", intake_reason="unit_test"
    )
    expected = _build_inbox_item(
        workspace_id=workspace.id,
        repo_id=None,
        run_id=None,
        play_key=None,
        effective_type=item_type,
        finding=finding,
        resolved=resolved,
    )

    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title=title,
        body=summary,
        level=level,
        dedup_key="dedup:ELS-1",
        payload=payload,
        inbox_overrides={
            "type": item_type,
            "intake_reason": "unit_test",
            "derive_classification": True,
            "derive_headline": True,
        },
        settings=_settings(False),
    )
    assert result.ok
    items = await _items_for(db_session, workspace.id)
    assert len(items) == 1
    got = items[0]
    for field_name in (
        "type", "title", "summary", "headline", "intake_handle",
        "intake_reason", "payload", "category", "priority", "status",
        "auto_resolvable", "stale_after",
    ):
        assert getattr(got, field_name) == getattr(expected, field_name), field_name


@pytest.mark.asyncio
async def test_default_routing_is_inbox_only(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="t",
        body="b",
        level=NotifyLevel.BLOCKER,
        settings=_settings(False),
    )
    assert [r.channel for r in result.results] == [NotifyChannel.INBOX]
    assert result.ok


@pytest.mark.asyncio
async def test_kill_switch_off_ignores_workspace_optin(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {
            "email_to": "ops@example.com",
            "channels": {"blocker": ["inbox", "linear", "email"]},
        }
    }
    await db_session.flush()
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="t",
        body="b",
        level=NotifyLevel.BLOCKER,
        ticket_ref="ELS-1",
        settings=_settings(False),
    )
    assert [r.channel for r in result.results] == [NotifyChannel.INBOX]


# ---------------------------------------------------------------------------
# Linear channel
# ---------------------------------------------------------------------------


class _RecordingGateway:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    async def comment(self, ref, *, body: str) -> None:
        self.comments.append((ref.id, body))


@pytest.mark.asyncio
async def test_linear_channel_posts_comment(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {"channels": {"blocker": ["inbox", "linear"]}}
    }
    await db_session.flush()

    gateway = _RecordingGateway()

    async def fake_resolve(**kwargs):
        return SimpleNamespace(kind="linear", gateway=gateway)

    monkeypatch.setattr(
        "backend.app.services.tracker_resolver.resolve_for_workspace",
        fake_resolve,
    )
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="Blocked at code_review",
        body="Gate refused the transition.",
        level=NotifyLevel.BLOCKER,
        ticket_ref="ELS-42",
        settings=_settings(True),
    )
    assert result.ok
    linear = result.for_channel(NotifyChannel.LINEAR)
    assert linear is not None and linear.ok and not linear.skipped
    assert gateway.comments and gateway.comments[0][0] == "ELS-42"
    assert "Blocked at code_review" in gateway.comments[0][1]


@pytest.mark.asyncio
async def test_linear_channel_skips_without_ticket(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {"channels": {"blocker": ["inbox", "linear"]}}
    }
    await db_session.flush()
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="t",
        body="b",
        level=NotifyLevel.BLOCKER,
        ticket_ref=None,
        settings=_settings(True),
    )
    linear = result.for_channel(NotifyChannel.LINEAR)
    assert linear is not None and linear.ok and linear.skipped
    assert linear.detail == "skipped_no_ticket"


# ---------------------------------------------------------------------------
# Email channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_channel_sends_through_recording_sender(
    db_session, seed_workspace, monkeypatch
) -> None:
    from backend.app.services.email.sender import RecordingEmailSender

    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {
            "email_to": "ops@example.com",
            "channels": {"blocker": ["inbox", "email"]},
        }
    }
    await db_session.flush()
    recorder = RecordingEmailSender()
    monkeypatch.setattr(
        "backend.app.services.email.sender.get_email_sender",
        lambda settings=None: recorder,
    )
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="Self-heal could not fix",
        body="Run failed twice.",
        level=NotifyLevel.BLOCKER,
        ticket_ref="ELS-7",
        settings=_settings(True),
    )
    email = result.for_channel(NotifyChannel.EMAIL)
    assert email is not None and email.ok and not email.skipped
    assert len(recorder.messages) == 1
    msg = recorder.messages[0]
    assert msg.to.email == "ops@example.com"
    assert "Self-heal could not fix" in msg.subject
    assert msg.text and "Run failed twice." in msg.text


@pytest.mark.asyncio
async def test_email_channel_structured_skip_without_recipient(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {"channels": {"blocker": ["inbox", "email"]}}
    }
    await db_session.flush()
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="t",
        body="b",
        level=NotifyLevel.BLOCKER,
        settings=_settings(True),
    )
    email = result.for_channel(NotifyChannel.EMAIL)
    assert email is not None and email.ok and email.skipped
    assert email.detail == "skipped_no_email_to"


# ---------------------------------------------------------------------------
# Fan-out isolation — one channel failing never sinks the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_exception_does_not_stop_fanout(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    workspace.settings = {
        "notifications": {
            "email_to": "ops@example.com",
            "channels": {"blocker": ["inbox", "linear", "email"]},
        }
    }
    await db_session.flush()

    async def exploding_resolve(**kwargs):
        raise RuntimeError("linear is down")

    from backend.app.services.email.sender import RecordingEmailSender

    recorder = RecordingEmailSender()
    monkeypatch.setattr(
        "backend.app.services.tracker_resolver.resolve_for_workspace",
        exploding_resolve,
    )
    monkeypatch.setattr(
        "backend.app.services.email.sender.get_email_sender",
        lambda settings=None: recorder,
    )
    result = await notify(
        db_session,
        workspace_id=workspace.id,
        title="t",
        body="b",
        level=NotifyLevel.BLOCKER,
        ticket_ref="ELS-9",
        settings=_settings(True),
    )
    # notify() returned (did not raise); linear failed; inbox + email ok.
    linear = result.for_channel(NotifyChannel.LINEAR)
    assert linear is not None and not linear.ok
    assert "linear is down" in (linear.detail or "")
    assert result.for_channel(NotifyChannel.INBOX).ok
    assert result.for_channel(NotifyChannel.EMAIL).ok
    assert len(recorder.messages) == 1
    items = await _items_for(db_session, workspace.id)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_notify_survives_missing_workspace(db_session) -> None:
    result = await notify(
        db_session,
        workspace_id=uuid.uuid4(),
        title="t",
        body="b",
        level=NotifyLevel.INFO,
        settings=_settings(True),
    )
    # Falls back to default inbox-only routing; inbox insert will fail FK
    # at flush time in real life, but notify() itself must not raise.
    assert [r.channel for r in result.results] == [NotifyChannel.INBOX]
    await db_session.rollback()


@pytest.mark.asyncio
async def test_omitted_classification_gets_server_defaults(
    db_session, seed_workspace
) -> None:
    """Flip byte-identity: sites that omit category/priority/headline
    today land DB server defaults (attention / 50 / NULL) — the channel
    must not silently 'improve' them."""
    _, _, workspace = seed_workspace
    await notify(
        db_session,
        workspace_id=workspace.id,
        title="blocked at stage",
        body="details",
        level=NotifyLevel.BLOCKER,
        settings=_settings(False),
    )
    await db_session.flush()
    items = await _items_for(db_session, workspace.id)
    assert len(items) == 1
    got = items[0]
    await db_session.refresh(got)
    assert got.category == "attention"
    assert got.priority == 50
    # headline is auto-derived by the model's before_insert backstop —
    # identical for the channel and for today's direct constructions.
    from backend.app.services.inbox.headline import derive_headline

    assert got.headline == derive_headline(summary="details", title="blocked at stage")
    assert got.type == "blocker"
