"""telegram_pending_actions — durable inline-keyboard store (ELS-252)

Replaces the bot's process-local ``_CHOICE_CACHE``: option lists now
survive leader failover, and the row id + server-side nonce back the
signed single-use ``callback_data`` (ELS-253). Forward revision after
0087 (applied in prod; never edited in place).

Revision ID: 0088_telegram_pending_actions
Revises: 0087_agent_provider_ship
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0088_telegram_pending_actions"
down_revision: Union[str, None] = "0087_agent_provider_ship"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_pending_actions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ship_thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "options",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("token_nonce", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("token_nonce", name="uq_telegram_pending_nonce"),
    )
    op.create_index(
        "ix_telegram_pending_chat_message",
        "telegram_pending_actions",
        ["telegram_chat_id", "bot_message_id"],
    )
    op.create_index(
        "ix_telegram_pending_workspace",
        "telegram_pending_actions",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_pending_workspace", table_name="telegram_pending_actions"
    )
    op.drop_index(
        "ix_telegram_pending_chat_message",
        table_name="telegram_pending_actions",
    )
    op.drop_table("telegram_pending_actions")
