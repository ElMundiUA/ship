"""Add inbox_items.headline with backfill (ELS-145).

Revision ID: 0069_inbox_items_headline
Revises: 0068_routine_runs_fk_hotfix
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0069_inbox_items_headline"
down_revision: Union[str, None] = "0068_routine_runs_fk_hotfix"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "inbox_items",
        sa.Column("headline", sa.String(length=80), nullable=True),
    )
    op.execute(
        """
        UPDATE inbox_items
        SET headline = LEFT(
            COALESCE(
                NULLIF(
                    TRIM(
                        SPLIT_PART(
                            REPLACE(COALESCE(summary, ''), E'\\r\\n', E'\\n'),
                            E'\\n',
                            1
                        )
                    ),
                    ''
                ),
                COALESCE(title, '')
            ),
            80
        )
        WHERE headline IS NULL
        """
    )
    op.execute(
        """
        UPDATE inbox_items
        SET headline = LEFT(COALESCE(title, 'Inbox item'), 80)
        WHERE headline IS NULL OR TRIM(headline) = ''
        """
    )
    op.alter_column("inbox_items", "headline", nullable=False)


def downgrade() -> None:
    op.drop_column("inbox_items", "headline")
