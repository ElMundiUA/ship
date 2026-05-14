"""E17 — chat_threads.memory_enabled toggle.

Per-thread flag the Console "Pause memory" button writes into. When
``false``, the chat-turn handler skips ``memory.add`` for that
thread's user messages. Useful when the PO wants a strictly
anonymous chat (debugging a side topic, sharing sensitive data that
shouldn't land in the extracted-facts store).

Default ``true`` so existing threads keep the current
"auto-extract everything" behaviour the moment ELS-127 lands.

Revision ID: 0071_chat_threads_memory_enabled
Revises: 0070_navigator_memories
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0071_chat_threads_memory_enabled"
down_revision: Union[str, None] = "0070_navigator_memories"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "memory_enabled")
