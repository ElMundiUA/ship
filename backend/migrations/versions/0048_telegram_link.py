"""telegram_chat_link + telegram_thread_map: Telegram group ↔ workspace bridge.

Revision ID: 0048_telegram_link
Revises: 0047_ws_default_agent
Create Date: 2026-05-03

Backs the Telegram bot adapter that lets a Ship workspace be addressed
from a Telegram group. Two tables:

  - ``telegram_chat_link`` — one row per bound Telegram chat. The chat
    is bound to exactly one workspace, by exactly one inviter. The
    service PAT under which the bot calls Navigator on behalf of this
    chat is stored as a FK so revoking the PAT severs the bridge.

  - ``telegram_thread_map`` — maps every outbound bot message to the
    Navigator ChatThread it belongs to. The bot consults this on each
    incoming message: if the user replied to a bot message that's in
    the map, the same thread continues; otherwise a fresh ``@mention``
    starts a new thread. We register *every* outbound bot message
    (not just the first one in a chain) because Telegram lets users
    reply to any message in the conversation.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0048_telegram_link"
down_revision: Union[str, None] = "0047_ws_default_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_chat_link",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Telegram chat IDs are signed 64-bit (groups negative, supergroups
        # like -100xxx). UNIQUE because one chat ↔ one workspace.
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "linked_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Service PAT the bot uses for /chat/stream calls on behalf of
        # this chat. Nullable + SET NULL so an admin revoking the PAT
        # in Console safely disables the bridge instead of orphaning it.
        sa.Column(
            "service_pat_id",
            UUID(as_uuid=True),
            sa.ForeignKey("api_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Cached at bind time for the Console UI ("Linked group: Shipyard").
        sa.Column("title", sa.String(256), nullable=True),
        # Bot worker process is separate from the API and must recover the
        # raw PAT after restart. ``api_tokens`` only stores a one-way hash,
        # so we keep the plaintext encrypted at rest here. Using the same
        # encrypt/decrypt machinery (Fernet via ``security.encryption``) as
        # ``Integration.secret_ciphertext``.
        sa.Column("pat_secret_ciphertext", sa.LargeBinary, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("telegram_chat_id", name="uq_telegram_chat_link_chat_id"),
    )
    op.create_index(
        "ix_telegram_chat_link_workspace_id",
        "telegram_chat_link",
        ["workspace_id"],
    )

    op.create_table(
        "telegram_thread_map",
        # Composite PK — (chat, bot_message) is unique by Telegram's own
        # identifier scheme.
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ship_thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "telegram_chat_id",
            "bot_message_id",
            name="pk_telegram_thread_map",
        ),
    )
    # Frequent lookup: "give me all bot message IDs in this thread"
    # (e.g. for cleanup if a thread is archived).
    op.create_index(
        "ix_telegram_thread_map_thread",
        "telegram_thread_map",
        ["ship_thread_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_thread_map_thread", table_name="telegram_thread_map")
    op.drop_table("telegram_thread_map")
    op.drop_index(
        "ix_telegram_chat_link_workspace_id", table_name="telegram_chat_link"
    )
    op.drop_table("telegram_chat_link")
