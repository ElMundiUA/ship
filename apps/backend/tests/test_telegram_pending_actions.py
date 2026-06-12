"""P7 chat-edge hardening (ELS-252/253/254).

- Durable pending-action store: option lists survive "leader
  failover" (no process memory involved at all — write and claim are
  pure DB), single-use claim is atomic, expired rows can't be
  claimed.
- Signed callback_data: build/parse/verify round-trip, tamper
  rejection, legacy ``c|<idx>`` payloads parse as None (stale), and
  the wire format stays under Telegram's 64-byte cap.
- Approval boundary: approval/control-stakes directives render URL
  deep-links into the Console Inbox; low-stakes options render signed
  callback buttons. Chat never commits the control plane.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.config import Settings
from backend.app.db.models.telegram import TelegramPendingAction
from backend.app.integrations.telegram.bot import (
    _build_choice_markup,
    _claim_pending_action,
    _collect_directive_actions,
    _store_pending_action,
)
from backend.app.integrations.telegram.callback_token import (
    build_callback_data,
    parse_callback_data,
    sign_callback,
    verify_callback,
)
from backend.app.integrations.telegram.render import Directive


def _settings() -> Settings:
    return Settings(OPENAI_API_KEY="test", JWT_SECRET="unit-secret")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# callback_token (ELS-253)
# ---------------------------------------------------------------------------


def test_callback_round_trip_and_length() -> None:
    settings = _settings()
    action_id = uuid.uuid4()
    nonce = "n" * 32
    data = build_callback_data(settings, action_id=action_id, idx=7, nonce=nonce)
    assert len(data.encode("utf-8")) <= 64
    parsed = parse_callback_data(data)
    assert parsed is not None
    assert parsed.action_id == action_id
    assert parsed.idx == 7
    assert verify_callback(settings, parsed=parsed, nonce=nonce) is True


def test_tampered_callback_fails_verification() -> None:
    settings = _settings()
    action_id = uuid.uuid4()
    nonce = "abc"
    data = build_callback_data(settings, action_id=action_id, idx=0, nonce=nonce)
    # Flip the option index — the signature no longer matches.
    forged = data.rsplit("|", 2)
    forged = f"{forged[0]}|1|{forged[2]}"
    parsed = parse_callback_data(forged)
    assert parsed is not None
    assert verify_callback(settings, parsed=parsed, nonce=nonce) is False
    # Wrong nonce (e.g. replay against a different row) also fails.
    parsed_ok = parse_callback_data(data)
    assert verify_callback(settings, parsed=parsed_ok, nonce="other") is False


def test_legacy_unsigned_payload_parses_as_none() -> None:
    assert parse_callback_data("c|0") is None
    assert parse_callback_data("c|not-a-uuid|0|sig") is None
    assert parse_callback_data("") is None


def test_signature_is_deterministic_per_inputs() -> None:
    settings = _settings()
    action_id = uuid.uuid4()
    assert sign_callback(
        settings, action_id=action_id, idx=1, nonce="x"
    ) == sign_callback(settings, action_id=action_id, idx=1, nonce="x")
    assert sign_callback(
        settings, action_id=action_id, idx=1, nonce="x"
    ) != sign_callback(settings, action_id=action_id, idx=2, nonce="x")


# ---------------------------------------------------------------------------
# Durable store (ELS-252)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_survive_failover_and_claim_once(
    db_session, seed_workspace
) -> None:
    """Store → claim reads from the DB (no process memory), and the
    second claim — a double click or Telegram re-delivery — loses."""
    _, _, workspace = seed_workspace
    options = [{"label": "Deploy", "value": "deploy"}]
    action_id, nonce = await _store_pending_action(
        db_session,
        workspace_id=workspace.id,
        chat_id=-100123,
        message_id=42,
        thread_id=None,
        options=options,
    )
    assert nonce

    # "New leader process": nothing held in memory — the claim is a
    # pure DB read+update on the id from callback_data.
    row = await _claim_pending_action(db_session, action_id=action_id)
    assert row is not None
    assert row.options == options
    assert row.consumed_at is not None

    again = await _claim_pending_action(db_session, action_id=action_id)
    assert again is None


@pytest.mark.asyncio
async def test_expired_action_cannot_be_claimed(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    row = TelegramPendingAction(
        workspace_id=workspace.id,
        telegram_chat_id=-100123,
        bot_message_id=43,
        options=[{"label": "x", "value": "x"}],
        token_nonce=f"nonce-{uuid.uuid4().hex}",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(row)
    await db_session.flush()

    assert await _claim_pending_action(db_session, action_id=row.id) is None


# ---------------------------------------------------------------------------
# Approval boundary (ELS-254)
# ---------------------------------------------------------------------------


_WS = uuid.UUID("00000000-0000-0000-0000-00000000abcd")


def test_approval_directive_renders_url_button() -> None:
    """AC: approval/control-stakes → URL deep-link into the Inbox
    item, NOT a callback button."""
    directives = [
        Directive(
            kind="ship-choice",
            payload={
                "approval": True,
                "label": "Approve deploy",
                "inbox_item_id": "11111111-2222-3333-4444-555555555555",
            },
            raw="",
        )
    ]
    options, links = _collect_directive_actions(
        directives, console_url="https://app.ship.test", workspace_id=_WS
    )
    assert options == []
    assert len(links) == 1
    assert links[0]["label"] == "Approve deploy"
    assert links[0]["url"] == (
        f"https://app.ship.test/inbox?ws={_WS}"
        "&selected=11111111-2222-3333-4444-555555555555"
    )

    markup = _build_choice_markup(options, links, sign=lambda i: "c|x")
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.url == links[0]["url"]
    assert button.callback_data is None


def test_stakes_control_also_routes_to_url() -> None:
    directives = [
        Directive(
            kind="ship-choice",
            payload={"stakes": "control", "title": "Unblock merge"},
            raw="",
        )
    ]
    options, links = _collect_directive_actions(
        directives, console_url="https://app.ship.test", workspace_id=_WS
    )
    assert options == []
    assert links[0]["url"].startswith(f"https://app.ship.test/inbox?ws={_WS}")


def test_low_stakes_options_render_signed_callbacks() -> None:
    """AC: pre-enumerated Navigator choices keep round-tripping as
    callback buttons, signed via the provided signer."""
    directives = [
        Directive(
            kind="ship-choice",
            payload={"options": ["Option A", {"label": "Option B", "value": "b"}]},
            raw="",
        )
    ]
    options, links = _collect_directive_actions(
        directives, console_url="https://app.ship.test", workspace_id=_WS
    )
    assert links == []
    assert [o["value"] for o in options] == ["Option A", "b"]

    signed: list[int] = []

    def _sign(idx: int) -> str:
        signed.append(idx)
        return f"c|deadbeefdeadbeefdeadbeefdeadbeef|{idx}|sig"

    markup = _build_choice_markup(options, links, sign=_sign)
    assert markup is not None
    assert signed == [0, 1]
    for row in markup.inline_keyboard:
        assert row[0].callback_data is not None
        assert row[0].url is None


def test_mixed_turn_renders_both_kinds() -> None:
    directives = [
        Directive(
            kind="ship-choice",
            payload={"options": ["Continue locally"]},
            raw="",
        ),
        Directive(
            kind="ship-choice",
            payload={"approval": True, "label": "Approve in Inbox"},
            raw="",
        ),
    ]
    options, links = _collect_directive_actions(
        directives, console_url="https://app.ship.test", workspace_id=_WS
    )
    markup = _build_choice_markup(options, links, sign=lambda i: f"c|a|{i}|s")
    assert markup is not None
    kinds = [
        "url" if b.url else "callback"
        for row in markup.inline_keyboard
        for b in row
    ]
    assert kinds == ["callback", "url"]
