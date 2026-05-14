"""E17/ELS-128 — cache the most recent mem0 retrieval per thread.

When the chat-turn handler runs the smart-trigger retrieval
(``mem0.search`` on first turn or after a 30+ min idle gap), the
returned fact ids land here so:

- The Console UI can render "Using N memories" without re-issuing
  the search on every re-render.
- A reconnecting client doesn't double-search just to repaint the
  disclosure.
- Operators tracking memory-health metrics can read the cache
  directly instead of replaying the search.

JSONB array of UUID strings — keeps the API flat ("list of
navigator_memories.id") without modelling a separate table for what
is effectively transient working state.

Revision ID: 0072_chat_threads_last_retrieved_facts
Revises: 0071_chat_threads_memory_enabled
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0072_chat_threads_last_retrieved"
down_revision: Union[str, None] = "0071_chat_threads_memory_enabled"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column(
            "last_retrieved_facts",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chat_threads",
        sa.Column(
            "last_retrieved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "last_retrieved_at")
    op.drop_column("chat_threads", "last_retrieved_facts")
