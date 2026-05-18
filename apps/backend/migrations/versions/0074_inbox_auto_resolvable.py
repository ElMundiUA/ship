"""Inbox auto-resolvable rows + stale_after (ELS-144).

Adds columns used to auto-close recoverable inbox noise when a ticket
advances and to time-box rows via the INBOX_STALE_SWEEP cron.

Revision ID: 0074_inbox_auto_resolvable
Revises: 0073_local_memory_adapters
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0074_inbox_auto_resolvable"
down_revision: Union[str, None] = "0073_local_memory_adapters"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "inbox_items",
        sa.Column(
            "auto_resolvable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "inbox_items",
        sa.Column("stale_after", sa.Interval(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inbox_items", "stale_after")
    op.drop_column("inbox_items", "auto_resolvable")
