"""Telegram bot worker — long-poll bridge from group chats to Navigator.

Run with:

    python -m backend.app.integrations.telegram.bot

Requires ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_BOT_USERNAME`` in env.
The bot uses long-polling so no public webhook URL is needed; this is
the laptop-friendly default. Webhook mode can be added later as a
FastAPI route that hands updates to the same dispatcher.

Triggers (Telegram bot privacy mode is left ON by default — the bot
only sees these in groups):

  - ``@<bot_username>`` mention            → fresh Navigator thread
  - ``/ask <prompt>`` slash command        → fresh Navigator thread
  - reply to any prior bot message         → continue that thread
  - ``/link``                              → bind flow (admin DM with deep-link)

Identity model is shared workspace — every message in a bound group
runs under the same service PAT, regardless of which Telegram user
sent it. The ``/link`` command therefore checks Telegram-side group
admin role; the workspace-side admin check happens in the Console
``/bind/confirm`` endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.telegram import TelegramChatLink, TelegramThreadMap
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.telegram.bind_state import build_bind_nonce
from backend.app.integrations.telegram.render import (
    markdown_to_telegram_html,
    render_with_directives_inline,
    split_markdown_into_chunks,
)
from backend.app.security.encryption import decrypt


logger = logging.getLogger(__name__)


# Telegram rate limits per chat: ~1 edit/second is safe; we batch
# deltas into edits at this cadence so a 30-token-per-second LLM
# doesn't fan out into 30 edits/sec and trip 429s.
_EDIT_INTERVAL_SECONDS: float = 1.2

_PLACEHOLDER_TEXT: str = "…"
_TOOL_PREFIX: str = "🔧 "


# ---------------------------------------------------------------------------
# Link + thread resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LinkContext:
    """In-memory view of a TelegramChatLink, with PAT already decrypted."""

    workspace_id: uuid.UUID
    pat_secret: str


async def _get_link(session: AsyncSession, chat_id: int) -> _LinkContext | None:
    row = (
        await session.execute(
            select(TelegramChatLink).where(
                TelegramChatLink.telegram_chat_id == chat_id
            )
        )
    ).scalar_one_or_none()
    if row is None or row.pat_secret_ciphertext is None:
        return None
    return _LinkContext(
        workspace_id=row.workspace_id,
        pat_secret=decrypt(row.pat_secret_ciphertext),
    )


async def _resolve_thread_id(
    session: AsyncSession,
    *,
    chat_id: int,
    reply_to_message_id: int | None,
) -> uuid.UUID | None:
    """If the user replied to a known bot message, return its Navigator thread."""
    if reply_to_message_id is None:
        return None
    row = (
        await session.execute(
            select(TelegramThreadMap.ship_thread_id).where(
                TelegramThreadMap.telegram_chat_id == chat_id,
                TelegramThreadMap.bot_message_id == reply_to_message_id,
            )
        )
    ).scalar_one_or_none()
    return row


async def _register_bot_message(
    session: AsyncSession,
    *,
    chat_id: int,
    bot_message_id: int,
    ship_thread_id: uuid.UUID,
) -> None:
    session.add(
        TelegramThreadMap(
            telegram_chat_id=chat_id,
            bot_message_id=bot_message_id,
            ship_thread_id=ship_thread_id,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Navigator API client (SSE consumer)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _TurnEvent:
    """Internal projection of an SSE event the bot cares about."""

    kind: str  # "thread" | "delta" | "tool_call" | "tool_result" | "end" | "error"
    text: str = ""
    thread_id: uuid.UUID | None = None
    tool_name: str = ""


async def _stream_navigator_turn(
    *,
    api_base: str,
    workspace_id: uuid.UUID,
    pat: str,
    body: str,
    thread_id: uuid.UUID | None,
) -> AsyncIterator[_TurnEvent]:
    """Open chat/stream SSE; yield only the events the bot needs to render."""
    url = f"{api_base.rstrip('/')}/v1/workspaces/{workspace_id}/chat/stream"
    payload: dict[str, object] = {"body": body, "classify_shift": False}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "text/event-stream",
    }
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                err_body = (await resp.aread()).decode("utf-8", errors="replace")
                yield _TurnEvent(
                    kind="error",
                    text=f"HTTP {resp.status_code}: {err_body[:400]}",
                )
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "thread":
                    raw_id = (event.get("thread") or {}).get("id")
                    if raw_id:
                        yield _TurnEvent(
                            kind="thread", thread_id=uuid.UUID(str(raw_id))
                        )
                elif kind == "delta":
                    yield _TurnEvent(kind="delta", text=event.get("text", ""))
                elif kind == "tool_call":
                    yield _TurnEvent(
                        kind="tool_call", tool_name=str(event.get("name", "tool"))
                    )
                elif kind == "tool_result":
                    yield _TurnEvent(
                        kind="tool_result", tool_name=str(event.get("name", ""))
                    )
                elif kind == "end":
                    yield _TurnEvent(kind="end")
                    return
                elif kind == "error":
                    yield _TurnEvent(
                        kind="error", text=str(event.get("error", "unknown error"))
                    )
                    return


# ---------------------------------------------------------------------------
# Turn driver
# ---------------------------------------------------------------------------


async def _drive_turn(
    *,
    bot: Bot,
    chat_id: int,
    placeholder_message_id: int,
    api_base: str,
    workspace_id: uuid.UUID,
    pat: str,
    body: str,
    seed_thread_id: uuid.UUID | None,
    sessionmaker,  # type: ignore[no-untyped-def]
) -> None:
    """Consume the SSE stream and render Navigator's reply into the chat.

    Splits long replies across multiple Telegram messages because the
    platform caps a single message at 4096 chars. The first chunk
    edits the original "…" placeholder in place; later chunks are sent
    as fresh messages and registered in ``telegram_thread_map`` so a
    user replying to *any* of them resolves to the same Navigator
    thread.

    Markdown coming out of Navigator is converted to Telegram's HTML
    subset on every edit (headers→bold, bullets→``• ``, inline code,
    fenced ``<pre>``) so the user sees rendered formatting instead of
    raw asterisks. Choice / todo directives that Console renders as
    interactive cards fall back to a plain-markdown summary here —
    Stage 2 swaps that for ``InlineKeyboardMarkup``.
    """
    buf = ""
    active_tool: str | None = None
    last_edit_at = 0.0
    thread_id: uuid.UUID | None = seed_thread_id
    thread_registered = seed_thread_id is not None

    # ``messages`` mirrors the bot messages making up this turn, in
    # chat order. ``rendered`` caches the last HTML we wrote per
    # message so we can skip identical edits (Telegram 400s on
    # ``MESSAGE_NOT_MODIFIED``).
    messages: list[int] = [placeholder_message_id]
    rendered: list[str] = [""]

    async def _register_continuation(message_id: int) -> None:
        if thread_id is None:
            return
        try:
            async with sessionmaker() as session, session.begin():
                await _register_bot_message(
                    session,
                    chat_id=chat_id,
                    bot_message_id=message_id,
                    ship_thread_id=thread_id,
                )
        except Exception:  # noqa: BLE001 — registration is best-effort
            logger.exception(
                "telegram thread-map registration failed (chat=%s msg=%s)",
                chat_id,
                message_id,
            )

    async def maybe_edit(*, force: bool = False) -> None:
        nonlocal last_edit_at
        now = time.monotonic()
        if not force and (now - last_edit_at) < _EDIT_INTERVAL_SECONDS:
            return

        cleaned_md, _directives = render_with_directives_inline(buf)
        if active_tool:
            tool_line = f"{_TOOL_PREFIX}{active_tool}"
            cleaned_md = (cleaned_md + "\n\n" + tool_line).strip()
        if not cleaned_md:
            cleaned_md = _PLACEHOLDER_TEXT

        chunks = split_markdown_into_chunks(cleaned_md) or [_PLACEHOLDER_TEXT]
        chunk_html = [markdown_to_telegram_html(c) for c in chunks]

        for i, html in enumerate(chunk_html):
            if i < len(messages):
                if rendered[i] == html:
                    continue
                try:
                    await bot.edit_message_text(
                        html,
                        chat_id=chat_id,
                        message_id=messages[i],
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    rendered[i] = html
                except Exception as exc:  # noqa: BLE001 — TG can 400 on benign edits
                    logger.debug(
                        "telegram edit failed (chat=%s msg=%s): %s",
                        chat_id,
                        messages[i],
                        exc,
                    )
            else:
                try:
                    sent = await bot.send_message(
                        chat_id,
                        html,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "telegram continuation send failed (chat=%s): %s",
                        chat_id,
                        exc,
                    )
                    break
                messages.append(sent.message_id)
                rendered.append(html)
                await _register_continuation(sent.message_id)

        last_edit_at = now

    try:
        async for ev in _stream_navigator_turn(
            api_base=api_base,
            workspace_id=workspace_id,
            pat=pat,
            body=body,
            thread_id=seed_thread_id,
        ):
            if ev.kind == "thread" and not thread_registered and ev.thread_id:
                thread_id = ev.thread_id
                async with sessionmaker() as session, session.begin():
                    await _register_bot_message(
                        session,
                        chat_id=chat_id,
                        bot_message_id=placeholder_message_id,
                        ship_thread_id=ev.thread_id,
                    )
                thread_registered = True
            elif ev.kind == "delta":
                buf += ev.text
                await maybe_edit()
            elif ev.kind == "tool_call":
                active_tool = ev.tool_name
                await maybe_edit(force=True)
            elif ev.kind == "tool_result":
                active_tool = None
                await maybe_edit(force=True)
            elif ev.kind == "end":
                break
            elif ev.kind == "error":
                buf = (buf + "\n\n⚠️ " + ev.text).strip()
                break
    except httpx.HTTPError as exc:
        buf = (buf + f"\n\n⚠️ network error: {exc}").strip()

    active_tool = None
    if not buf:
        buf = "(navigator returned no text)"
    await maybe_edit(force=True)


# ---------------------------------------------------------------------------
# Aiogram handlers
# ---------------------------------------------------------------------------


def _is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def _strip_mention(text: str, bot_username: str) -> str:
    handle = f"@{bot_username}"
    if text.startswith(handle):
        return text[len(handle):].strip()
    # mention may also appear mid-text; for PoC we only handle leading mention
    return text.replace(handle, "", 1).strip()


def _build_link_url(*, console_url: str, nonce: str) -> str:
    return f"{console_url.rstrip('/')}/integrations/telegram/bind?nonce={nonce}"


async def _handle_link_command(
    message: Message, *, settings: Settings
) -> None:
    """``/link`` — generate a bind nonce, DM the admin a deep-link to Console.

    Must be called from inside a group (the chat being bound). The bot
    only posts a short ack in the group; the actual deep-link goes to
    the caller's private DM so it isn't visible to other group members.
    """
    if not _is_group(message):
        await message.answer(
            "Run /link from inside the group you want to bind to a Ship workspace."
        )
        return
    if message.from_user is None:
        return
    nonce = build_bind_nonce(
        chat_id=message.chat.id,
        chat_title=message.chat.title,
        settings=settings,
    )
    deep_link = _build_link_url(console_url=settings.console_url, nonce=nonce)
    try:
        await message.bot.send_message(
            chat_id=message.from_user.id,
            text=(
                f"To bind *{message.chat.title or 'this group'}* to a "
                f"Ship workspace, open:\n\n{deep_link}\n\n"
                "Link valid for 10 minutes."
            ),
            parse_mode="Markdown",
        )
        await message.reply(
            "📬 Sent you a private message with the bind link."
        )
    except Exception as exc:  # noqa: BLE001 — typically "user hasn't started bot in DM"
        logger.info("telegram /link DM failed: %s", exc)
        await message.reply(
            f"Couldn't DM you — open https://t.me/{settings.telegram_bot_username} "
            "and press Start, then run /link again."
        )


async def _handle_navigator_turn(
    message: Message,
    *,
    body: str,
    settings: Settings,
    sessionmaker,  # type: ignore[no-untyped-def]
) -> None:
    """Run one Navigator turn: resolve thread, send placeholder, stream into it."""
    if not _is_group(message):
        await message.answer(
            "I'm a workspace bot — add me to your team's Telegram group and "
            "run /link there to bind it."
        )
        return

    async with sessionmaker() as session, session.begin():
        link = await _get_link(session, message.chat.id)
        if link is None:
            await message.reply(
                "This group isn't bound to a Ship workspace yet. "
                "An admin can run /link to set it up."
            )
            return
        reply_to = (
            message.reply_to_message.message_id
            if message.reply_to_message is not None
            else None
        )
        seed_thread_id = await _resolve_thread_id(
            session, chat_id=message.chat.id, reply_to_message_id=reply_to
        )

    placeholder = await message.reply(_PLACEHOLDER_TEXT)

    await _drive_turn(
        bot=message.bot,
        chat_id=message.chat.id,
        placeholder_message_id=placeholder.message_id,
        api_base=settings.public_url,
        workspace_id=link.workspace_id,
        pat=link.pat_secret,
        body=body,
        seed_thread_id=seed_thread_id,
        sessionmaker=sessionmaker,
    )


def _build_dispatcher(settings: Settings) -> Dispatcher:
    sessionmaker = get_sessionmaker()
    bot_username = (settings.telegram_bot_username or "").lstrip("@")
    if not bot_username:
        raise RuntimeError(
            "TELEGRAM_BOT_USERNAME must be set (the bot's @handle, no leading @)"
        )

    dp = Dispatcher()

    @dp.message(Command("link"))
    async def on_link(message: Message) -> None:
        await _handle_link_command(message, settings=settings)

    @dp.message(Command("ask"))
    async def on_ask(message: Message) -> None:
        # Strip the leading "/ask" (and an optional bot suffix like
        # "/ask@your_bot"); whatever follows is the prompt.
        text = (message.text or "").split(maxsplit=1)
        body = text[1].strip() if len(text) > 1 else ""
        if not body:
            await message.reply("Usage: /ask <your question>")
            return
        await _handle_navigator_turn(
            message,
            body=body,
            settings=settings,
            sessionmaker=sessionmaker,
        )

    @dp.message(F.reply_to_message.from_user.is_bot.is_(True))
    async def on_reply(message: Message) -> None:
        body = (message.text or "").strip()
        if not body:
            return
        await _handle_navigator_turn(
            message,
            body=body,
            settings=settings,
            sessionmaker=sessionmaker,
        )

    @dp.message(F.text.contains(f"@{bot_username}"))
    async def on_mention(message: Message) -> None:
        body = _strip_mention(message.text or "", bot_username)
        if not body:
            await message.reply("Hi — ask me something after the mention.")
            return
        await _handle_navigator_turn(
            message,
            body=body,
            settings=settings,
            sessionmaker=sessionmaker,
        )

    return dp


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


# Postgres advisory-lock key — "SHIPBOT\x01" packed as a bigint. Every
# Ship-API replica races for this lock at startup; the winner runs the
# Telegram long-poll loop, the rest skip. Telegram returns 409 Conflict
# if more than one client polls the same bot, so single-leader matters
# the moment Bunny scales out past one replica (autoScaling.max=5).
_BOT_LEADER_LOCK_KEY: int = 0x5348495042_4F5401


async def _run_polling(settings: Settings, *, handle_signals: bool) -> None:
    """Open the bot, build the dispatcher, and long-poll until cancelled."""
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    bot = Bot(token=settings.telegram_bot_token)
    dp = _build_dispatcher(settings)
    me = await bot.get_me()
    logger.info("telegram bot online as @%s", me.username)
    try:
        await dp.start_polling(bot, handle_signals=handle_signals)
    finally:
        await bot.session.close()


# Wait between leader-lock attempts when another replica owns the lock
# (deploy rollovers, autoscale cycles). Short enough that bot downtime
# during a rolling restart is bounded to ~the old container's drain time
# plus this poll interval; long enough that the loop is essentially free.
_LEADER_RETRY_SECONDS: float = 30.0

# Cadence for the no-op keepalive query that keeps the held transaction
# from being killed by Postgres' ``idle_in_transaction_session_timeout``
# (Neon's default is 300s). 60s leaves plenty of headroom.
_LEADER_KEEPALIVE_SECONDS: float = 60.0

# Backoff after the polling loop crashes — gives Telegram / DNS / Neon
# time to settle before we re-acquire the lock and start fresh.
_POLL_CRASH_BACKOFF_SECONDS: float = 15.0


async def _leader_keepalive(conn: object) -> None:
    """Tick ``SELECT 1`` on the leader's held connection.

    Postgres terminates idle-in-transaction sessions after a timeout
    (Neon: 5 minutes). Without traffic on the connection holding the
    advisory lock, Postgres would kill the xact, drop the lock, and
    silently de-elect us — the polling loop would keep running but
    the lock would now be free for any racer to grab.
    """
    from sqlalchemy import text as _text

    while True:
        await asyncio.sleep(_LEADER_KEEPALIVE_SECONDS)
        await conn.execute(_text("SELECT 1"))  # type: ignore[attr-defined]


async def run_with_leader_lock(settings: Settings) -> None:
    """Re-elect a leader and run the bot in a loop until cancelled.

    Each iteration:
      1. Open a dedicated connection, BEGIN an explicit transaction,
         and try ``pg_try_advisory_xact_lock``. PgBouncer (Neon pooled
         DSN) pins the connection to one upstream backend for as long
         as the transaction stays open.
      2. If the lock is held by another replica, sleep for
         ``_LEADER_RETRY_SECONDS`` and try again — this is what makes
         a rolling deploy survive: the new replica patiently waits for
         the old one's drain instead of giving up forever.
      3. If we win the lock, kick off a keepalive task to keep the
         transaction alive past Neon's idle-in-transaction timeout,
         then run the long-poll. On exit (clean or crashed) the
         transaction unwinds, the lock auto-releases, and the next
         iteration of the loop tries again.

    Cancellation propagates cleanly: the ``async with`` blocks
    ROLLBACK + close on cancel, releasing the lock for the next
    replica before we exit.
    """
    if not settings.telegram_bot_token or not settings.telegram_bot_username:
        logger.info(
            "telegram bot disabled "
            "(TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME unset)"
        )
        return

    from sqlalchemy import text

    from backend.app.db.session import get_engine

    async def _try_lead_once() -> bool:
        """Attempt one leadership cycle. Returns True iff we ran polling."""
        engine = get_engine()
        async with engine.connect() as conn:
            async with conn.begin():
                got = (
                    await conn.execute(
                        text("SELECT pg_try_advisory_xact_lock(:k)"),
                        {"k": _BOT_LEADER_LOCK_KEY},
                    )
                ).scalar()
                if not got:
                    return False
                logger.info("telegram bot: leader lock acquired")
                keepalive = asyncio.create_task(
                    _leader_keepalive(conn),
                    name="ship.telegram.bot.keepalive",
                )
                try:
                    await _run_polling(settings, handle_signals=False)
                finally:
                    keepalive.cancel()
                    try:
                        await keepalive
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
        return True

    while True:
        try:
            ran_polling = await _try_lead_once()
        except asyncio.CancelledError:
            logger.info("telegram bot: shutting down, releasing leader lock")
            raise
        except Exception:
            logger.exception(
                "telegram bot crashed; retrying in %.0fs",
                _POLL_CRASH_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_POLL_CRASH_BACKOFF_SECONDS)
            continue
        if ran_polling:
            # ``start_polling`` doesn't normally return on its own — if
            # it did, treat it like a crash and re-elect after backoff.
            logger.info(
                "telegram bot: polling returned without error; "
                "re-acquiring lock after %.0fs",
                _POLL_CRASH_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_POLL_CRASH_BACKOFF_SECONDS)
        else:
            logger.debug(
                "telegram bot: another replica holds the leader lock; "
                "will retry in %.0fs",
                _LEADER_RETRY_SECONDS,
            )
            await asyncio.sleep(_LEADER_RETRY_SECONDS)


def main() -> None:
    """Standalone entrypoint — kept for local dev (``python -m …``).

    The cloud deployment runs the bot inside the Ship-API FastAPI
    lifespan instead (see :mod:`backend.app.main`); this entrypoint is
    only used by laptop dev when an operator wants to run the bot in
    its own process against a separate API.
    """

    async def _local_main() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        await _run_polling(get_settings(), handle_signals=True)

    asyncio.run(_local_main())


if __name__ == "__main__":
    main()
