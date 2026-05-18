"""inbox_items: category + priority for actionable badge and lanes (ELS-147)

Revision ID: 0074_inbox_category_priority
Revises: 0073_local_memory_adapters
Create Date: 2026-05-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0074_inbox_category_priority"
down_revision = "0073_local_memory_adapters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbox_items",
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default="decision_needed",
        ),
    )
    op.add_column(
        "inbox_items",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        UPDATE inbox_items SET category = CASE
            WHEN type = 'report' THEN 'attention'
            WHEN type IN ('failure', 'blocker', 'exception') THEN 'failure'
            ELSE 'decision_needed'
        END
        """
    )
    op.execute(
        """
        UPDATE inbox_items SET priority = CASE
            WHEN type IN ('failure', 'blocker') THEN 10
            WHEN type IN ('clarification', 'approval') THEN 8
            WHEN type = 'exception' THEN 6
            WHEN type = 'improvement' THEN 5
            ELSE 0
        END
        """
    )
    op.create_index(
        "ix_inbox_workspace_category_status",
        "inbox_items",
        ["workspace_id", "category", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_workspace_category_status", table_name="inbox_items")
    op.drop_column("inbox_items", "priority")
    op.drop_column("inbox_items", "category")
