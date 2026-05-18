"""Unit tests for Telegram bot helpers (offline / DB fixtures)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from aiogram.enums import ChatType

from backend.app.db.models.agent_surface import ChatThread
from backend.app.db.models.telegram import TelegramChatLink, TelegramThreadMap
from backend.app.integrations.telegram import bot as telegram_bot
from backend.app.security.encryption import encrypt


def test_strip_mention_leading() -> None:
    assert telegram_bot._strip_mention("@mybot hello", "mybot") == "hello"


def test_strip_mention_mid_text_first_occurrence() -> None:
    assert (
        telegram_bot._strip_mention("hi @mybot there", "mybot") == "hi  there"
    )


def test_build_link_url() -> None:
    url = telegram_bot._build_link_url(
        console_url="https://console.ship.test/", nonce="abc123"
    )
    assert (
        url
        == "https://console.ship.test/integrations/telegram/bind?nonce=abc123"
    )


def test_is_group_recognises_group_chats() -> None:
    group = MagicMock()
    group.chat.type = ChatType.GROUP
    supergroup = MagicMock()
    supergroup.chat.type = ChatType.SUPERGROUP
    private = MagicMock()
    private.chat.type = ChatType.PRIVATE
    assert telegram_bot._is_group(group) is True
    assert telegram_bot._is_group(supergroup) is True
    assert telegram_bot._is_group(private) is False


@pytest.mark.asyncio
async def test_get_link_returns_none_when_unbound(db_session) -> None:
    link = await telegram_bot._get_link(db_session, chat_id=-999001)
    assert link is None


@pytest.mark.asyncio
async def test_get_link_returns_decrypted_pat(
    db_session, seed_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _, workspace = seed_workspace
    chat_id = -1005550001
    pat_plain = "pat-secret-for-telegram-test"
    monkeypatch.setenv("JWT_SECRET", "test-secret-do-not-use-anywhere-real")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        row = TelegramChatLink(
            telegram_chat_id=chat_id,
            workspace_id=workspace.id,
            linked_by_user_id=user.id,
            pat_secret_ciphertext=encrypt(pat_plain),
        )
        db_session.add(row)
        await db_session.flush()

        link = await telegram_bot._get_link(db_session, chat_id=chat_id)
        assert link is not None
        assert link.workspace_id == workspace.id
        assert link.pat_secret == pat_plain
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_resolve_thread_id_unknown_reply(db_session) -> None:
    thread_id = await telegram_bot._resolve_thread_id(
        db_session, chat_id=-1005550002, reply_to_message_id=404
    )
    assert thread_id is None


@pytest.mark.asyncio
async def test_resolve_thread_id_returns_mapped_thread(
    db_session, seed_workspace
) -> None:
    user, _, workspace = seed_workspace
    ship_thread = ChatThread(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Telegram thread",
        status="active",
    )
    db_session.add(ship_thread)
    await db_session.flush()

    chat_id = -1005550003
    bot_message_id = 77
    db_session.add(
        TelegramThreadMap(
            telegram_chat_id=chat_id,
            bot_message_id=bot_message_id,
            ship_thread_id=ship_thread.id,
        )
    )
    await db_session.flush()

    resolved = await telegram_bot._resolve_thread_id(
        db_session,
        chat_id=chat_id,
        reply_to_message_id=bot_message_id,
    )
    assert resolved == ship_thread.id

    missing = await telegram_bot._resolve_thread_id(
        db_session, chat_id=chat_id, reply_to_message_id=999
    )
    assert missing is None
