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
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.telegram import (
    TelegramChatLink,
    TelegramPendingAction,
    TelegramThreadMap,
)
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.telegram.bind_state import build_bind_nonce
from backend.app.integrations.telegram.callback_token import (
    CALLBACK_TTL_SECONDS,
    build_callback_data,
    parse_callback_data,
    verify_callback,
)
from backend.app.integrations.telegram.render import (
    Directive,
    extract_directives,
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
# Choice keyboard cache + builder (Console's ``ship-choice`` directive
# ↔ Telegram InlineKeyboardMarkup)
# ---------------------------------------------------------------------------


# Durable pending-action store (ELS-252) — replaces the process-local
# ``_CHOICE_CACHE`` that died on every leader failover and left
# previously-attached keyboards dead-on-click. The option list lives
# in ``telegram_pending_actions``; ``callback_data`` carries only a
# signed pointer (ELS-253).


async def _store_pending_action(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    chat_id: int,
    message_id: int,
    thread_id: uuid.UUID | None,
    options: list[dict],
) -> tuple[uuid.UUID, str]:
    """Persist the option list; returns ``(action_id, nonce)`` for the
    callback signer."""
    nonce = secrets.token_urlsafe(24)
    row = TelegramPendingAction(
        workspace_id=workspace_id,
        telegram_chat_id=chat_id,
        bot_message_id=message_id,
        ship_thread_id=thread_id,
        options=options,
        token_nonce=nonce,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=CALLBACK_TTL_SECONDS),
    )
    session.add(row)
    await session.flush()
    return row.id, nonce


async def _claim_pending_action(
    session: AsyncSession, *, action_id: uuid.UUID
) -> TelegramPendingAction | None:
    """Atomically consume the action row (single-use, ELS-253).

    ``UPDATE … WHERE consumed_at IS NULL RETURNING`` means a double
    click / Telegram re-delivery loses the race deterministically —
    the second caller gets ``None`` and answers "already used". The
    TTL check rides the same statement so an expired row can never be
    claimed.
    """
    from sqlalchemy import update

    now = datetime.now(timezone.utc)
    row = (
        await session.execute(
            update(TelegramPendingAction)
            .where(
                TelegramPendingAction.id == action_id,
                TelegramPendingAction.consumed_at.is_(None),
                TelegramPendingAction.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(TelegramPendingAction)
        )
    ).scalar_one_or_none()
    return row


def _normalize_choice_option(raw_opt: object) -> dict | None:
    if isinstance(raw_opt, str):
        return {"label": raw_opt, "value": raw_opt}
    if isinstance(raw_opt, dict):
        label_raw = raw_opt.get("label")
        value_raw = raw_opt.get("value")
        label = str(label_raw).strip() if isinstance(label_raw, str) else ""
        value = (
            str(value_raw).strip() if isinstance(value_raw, str) else label
        )
        if not label:
            return None
        return {"label": label, "value": value or label}
    return None


def _is_approval_directive(directive: Directive) -> bool:
    """Boundary classifier (ELS-254, thesis 4): callback button =
    low-stakes Navigator choice; url button = control-plane/approval
    deep-link. A directive is approval/control-stakes when its payload
    says so — the Navigator marks Inbox-approval prompts with
    ``approval: true`` (or ``stakes: approval|control``)."""
    payload = directive.payload or {}
    if payload.get("approval") is True:
        return True
    return str(payload.get("stakes") or "").lower() in ("approval", "control")


def _approval_deep_link(
    payload: dict, *, console_url: str, workspace_id: uuid.UUID
) -> str:
    """Deep-link into the authoritative Inbox approval surface.

    The Inbox stays reachable in EVERY console mode (the Phase-4
    ``console.surface`` gate pins it), so this link can never orphan
    a pending approval. The real approval is recorded there under the
    acting user's identity — never under the shared group PAT.
    """
    base = console_url.rstrip("/")
    item_id = payload.get("inbox_item_id")
    if item_id:
        return f"{base}/inbox?ws={workspace_id}&selected={item_id}"
    href = payload.get("href")
    if isinstance(href, str) and href.startswith(base):
        return href
    return f"{base}/inbox?ws={workspace_id}"


def _collect_directive_actions(
    directives: list[Directive],
    *,
    console_url: str,
    workspace_id: uuid.UUID,
) -> tuple[list[dict], list[dict]]:
    """Split one turn's directives into ``(options, links)``.

    ``options`` are low-stakes pre-enumerated Navigator choices that
    render as signed callback buttons; ``links`` are approval /
    control-stakes actions that render as URL buttons deep-linking to
    the Console Inbox (ELS-254 — chat never commits the control
    plane).
    """
    options: list[dict] = []
    links: list[dict] = []
    for directive in directives:
        if directive.kind != "ship-choice" or not directive.payload:
            continue
        if _is_approval_directive(directive):
            url = _approval_deep_link(
                directive.payload,
                console_url=console_url,
                workspace_id=workspace_id,
            )
            label = str(
                directive.payload.get("label")
                or directive.payload.get("title")
                or "Review in Inbox"
            ).strip() or "Review in Inbox"
            links.append({"label": label, "url": url})
            continue
        for raw_opt in directive.payload.get("options") or []:
            normalized = _normalize_choice_option(raw_opt)
            if normalized is not None:
                options.append(normalized)
    return options, links


def _button_text(label: str) -> str:
    # Telegram caps the button text at 64 chars; truncate politely
    # with an ellipsis — the full label survives in the durable row
    # for the actual chat echo.
    return label if len(label) <= 64 else label[:63] + "…"


def _build_choice_markup(
    options: list[dict],
    links: list[dict],
    *,
    sign,  # type: ignore[no-untyped-def] — (idx: int) -> callback_data str
) -> InlineKeyboardMarkup | None:
    """Assemble the keyboard: one row per option (signed callback
    button) followed by one row per approval link (URL button).
    Returns ``None`` when there is nothing to render."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, opt in enumerate(options):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_button_text(opt["label"]),
                    callback_data=sign(idx),
                )
            ]
        )
    for link in links:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_button_text(link["label"]),
                    url=link["url"],
                )
            ]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Turn driver
# ---------------------------------------------------------------------------


# Telegram's ``sendChatAction`` advertisement expires after roughly
# 5 seconds; we re-send a bit faster so the "is typing…" indicator
# stays continuous through tool-call pauses (Linear / Notion / fs
# walks routinely stall text deltas for 10–30s).
_TYPING_REFRESH_SECONDS: float = 4.0


async def _typing_indicator(bot: Bot, chat_id: int) -> None:
    """Keep Telegram's "is typing…" affordance lit for the active turn.

    Best-effort by design — a transient ``sendChatAction`` failure
    (rate limit, network blip) shouldn't kill the turn, just skip a
    refresh. Cancellation lands at the ``await asyncio.sleep`` and
    propagates cleanly so ``_drive_turn``'s ``finally`` clause
    completes promptly.
    """
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001
            # Don't log — too chatty for a tick that runs every 4s.
            pass
        await asyncio.sleep(_TYPING_REFRESH_SECONDS)


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

    # Background "is typing…" pinger. Telegram's chat action expires
    # after ~5s, so we refresh it every ~4s for the duration of the
    # turn. This gives the user a continuous in-flight signal even
    # while Navigator is mid-tool-call (which can stall text deltas
    # for 10–30s while Linear / Notion / fs walks complete).
    typing_task = asyncio.create_task(
        _typing_indicator(bot, chat_id),
        name="ship.telegram.bot.typing",
    )

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

        # End-of-turn: attach an InlineKeyboardMarkup to the last bot
        # message if Navigator emitted any ship-choice directives.
        # Low-stakes options render as SIGNED callback buttons whose
        # option list persists in telegram_pending_actions (survives
        # leader failover, single-use); approval/control-stakes
        # directives render as URL deep-links into the Console Inbox
        # and never round-trip through the bot (ELS-252/253/254).
        _, final_directives = extract_directives(buf)
        settings = get_settings()
        options, links = _collect_directive_actions(
            final_directives,
            console_url=settings.console_url,
            workspace_id=workspace_id,
        )
        if (options or links) and messages:
            target_id = messages[-1]
            try:
                if options:
                    async with sessionmaker() as session, session.begin():
                        action_id, nonce = await _store_pending_action(
                            session,
                            workspace_id=workspace_id,
                            chat_id=chat_id,
                            message_id=target_id,
                            thread_id=thread_id,
                            options=options,
                        )

                    def _sign(idx: int) -> str:
                        return build_callback_data(
                            settings,
                            action_id=action_id,
                            idx=idx,
                            nonce=nonce,
                        )

                else:
                    def _sign(idx: int) -> str:  # pragma: no cover — no options
                        raise RuntimeError("no callback options to sign")

                markup = _build_choice_markup(options, links, sign=_sign)
                if markup is not None:
                    await bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=target_id,
                        reply_markup=markup,
                    )
            except Exception as exc:  # noqa: BLE001 — Telegram 400 on benign edits
                logger.debug(
                    "telegram attach choice markup failed (chat=%s msg=%s): %s",
                    chat_id,
                    target_id,
                    exc,
                )
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


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

    @dp.callback_query(F.data.startswith("c|"))
    async def on_choice_click(query: CallbackQuery) -> None:
        """Translate a ship-choice button click into a Navigator turn.

        ``callback_data`` is the signed pointer from ELS-253:
        ``c|<action_id>|<idx>|<sig>``. The option list lives in the
        durable ``telegram_pending_actions`` row (ELS-252), so clicks
        survive leader failover. Verification order: parse → load row
        → HMAC check against the row's nonce → ATOMIC single-use
        consume (+TTL) — only then does a Navigator turn run, so a
        double click / Telegram re-delivery fires exactly once.
        Legacy ``c|<idx>`` payloads from pre-failover keyboards parse
        as ``None`` and get the stale answer.
        """
        if (
            query.data is None
            or query.message is None
            or query.message.chat is None
            or query.message.message_id is None
            or query.bot is None
        ):
            await query.answer("Invalid click", show_alert=False)
            return

        chat_id = query.message.chat.id
        button_message_id = query.message.message_id

        async def _reject_stale() -> None:
            await query.answer(
                "Этот выбор уже не активен — напиши свой ответ текстом.",
                show_alert=True,
            )
            # Strip stale buttons to avoid further misleading clicks.
            try:
                await query.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=button_message_id,
                    reply_markup=None,
                )
            except Exception:  # noqa: BLE001
                pass

        parsed = parse_callback_data(query.data)
        if parsed is None:
            await _reject_stale()
            return

        async with sessionmaker() as session, session.begin():
            action = await session.get(TelegramPendingAction, parsed.action_id)
            if action is None or not verify_callback(
                settings, parsed=parsed, nonce=action.token_nonce
            ):
                # Unknown row or bad signature — forged/tampered
                # callback_data. No Navigator turn.
                action = None
            elif parsed.idx >= len(action.options or []):
                action = None
            else:
                # Atomic single-use claim; expired or already-consumed
                # rows come back None and the click is rejected.
                action = await _claim_pending_action(
                    session, action_id=parsed.action_id
                )

        if action is None:
            await _reject_stale()
            return

        chosen = (action.options or [])[parsed.idx]
        label = chosen.get("label") or chosen.get("value") or ""
        value = chosen.get("value") or label

        # Acknowledge the click first so the spinner stops; then strip
        # buttons so the same option can't fire twice.
        await query.answer()
        try:
            await query.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=button_message_id,
                reply_markup=None,
            )
        except Exception:  # noqa: BLE001
            pass

        # Echo so the conversation reads as "user picked: …" — clicks
        # don't otherwise produce a user-visible message in Telegram
        # (unlike typing), and an unattributed assistant reply would
        # leave the chat reading like a non-sequitur.
        try:
            await query.bot.send_message(
                chat_id=chat_id, text=f"✅ {label}"
            )
        except Exception:  # noqa: BLE001
            pass

        async with sessionmaker() as session, session.begin():
            link = await _get_link(session, chat_id)
            if link is None:
                return
            # The durable row caches the Navigator thread the buttons
            # came from; the telegram_thread_map lookup stays as the
            # fallback for rows attached before the thread event
            # arrived.
            seed_thread_id = action.ship_thread_id or await _resolve_thread_id(
                session,
                chat_id=chat_id,
                reply_to_message_id=button_message_id,
            )

        try:
            placeholder = await query.bot.send_message(
                chat_id=chat_id, text=_PLACEHOLDER_TEXT
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "telegram choice placeholder send failed (chat=%s)", chat_id
            )
            return

        await _drive_turn(
            bot=query.bot,
            chat_id=chat_id,
            placeholder_message_id=placeholder.message_id,
            api_base=settings.public_url,
            workspace_id=link.workspace_id,
            pat=link.pat_secret,
            body=value,
            seed_thread_id=seed_thread_id,
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
