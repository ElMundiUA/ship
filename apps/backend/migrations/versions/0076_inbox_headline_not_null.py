"""inbox headline backfill + NOT NULL (ELS-145).

After all write paths populate ``headline`` via ``derive_headline``, enforce
non-null at the database layer. Backfill legacy rows from the first line of
``summary``, else truncated ``title``.

Revision ID: 0076_inbox_headline_not_null
Revises: 0077_lock_run_id_bigint
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0076_inbox_headline_not_null"
down_revision: Union[str, None] = "0077_lock_run_id_bigint"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE inbox_items AS i
               SET headline = LEFT(
                     COALESCE(
                       NULLIF(
                         TRIM(
                           split_part(
                             replace(COALESCE(i.summary, ''), E'\\r\\n', E'\\n'),
                             E'\\n',
                             1
                           )
                         ),
                         ''
                       ),
                       COALESCE(NULLIF(TRIM(i.title), ''), 'Inbox item')
                     ),
                     80
                   )
             WHERE i.headline IS NULL
                OR TRIM(i.headline) = ''
            """
        )
    )
    op.alter_column(
        "inbox_items",
        "headline",
        existing_type=sa.String(length=80),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "inbox_items",
        "headline",
        existing_type=sa.String(length=80),
        nullable=True,
    )
