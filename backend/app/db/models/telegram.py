"""Telegram bot adapter tables: group ↔ workspace bridge + message ↔ thread map.

Backs the Telegram bot that lets a Ship workspace be addressed from a
Telegram group chat. See migration ``0048_telegram_link`` for the
schema rationale.

Identity model is *shared workspace* — every message in a bound group
runs under the same service PAT, so Navigator does not distinguish
which Telegram user spoke. Per-user mapping can be layered on later
without changing this schema (it would add a separate
``telegram_user_link`` table).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701  — shared column helper, intra-package.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


class TelegramChatLink(Base):
    """A Telegram group chat bound to a Ship workspace.

    ``telegram_chat_id`` is unique — one chat can only be linked to one
    workspace at a time. Re-binding to a different workspace updates
    the existing row (handled in the bind endpoint, not at the schema
    level, so we keep ``linked_by_user_id`` historically accurate).

    ``service_pat_id`` is nullable so revoking the PAT in Console
    doesn't cascade-delete the link row — instead the bot starts
    refusing to answer with a clear "your bridge has no token, rebind".
    """

    __tablename__ = "telegram_chat_link"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id", name="uq_telegram_chat_link_chat_id"
        ),
        Index("ix_telegram_chat_link_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    linked_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    service_pat_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Encrypted plaintext of the service PAT — bot worker decrypts on
    # startup so it survives restarts without admin re-binding. See
    # migration 0048 for the rationale.
    pat_secret_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class TelegramThreadMap(Base):
    """Maps every outbound bot message to its Navigator ChatThread.

    On every incoming user message, the bot looks at
    ``message.reply_to_message.message_id``: if that ID is in this
    table for the same chat, the existing ``ship_thread_id`` continues;
    otherwise a fresh ``@mention`` starts a new thread and we register
    the bot's first reply here.

    Composite PK ``(telegram_chat_id, bot_message_id)`` because
    Telegram message IDs are unique per chat, not globally.
    """

    __tablename__ = "telegram_thread_map"
    __table_args__ = (
        PrimaryKeyConstraint(
            "telegram_chat_id",
            "bot_message_id",
            name="pk_telegram_thread_map",
        ),
        Index("ix_telegram_thread_map_thread", "ship_thread_id"),
    )

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ship_thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = _ts_created()
