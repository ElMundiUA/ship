"""set gen_random_uuid() default on deployments.id

Revision ID: 0083_deployments_id_default
Revises: 0082_deployments_table
Create Date: 2026-06-01

0082 created ``deployments.id`` without a server default, so inserts that
rely on the DB to generate the PK (the ORM pattern used everywhere via
``_pk()``) violated the NOT NULL constraint. This backfills the default.
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0083_deployments_id_default"
down_revision: Union[str, None] = "0082_deployments_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deployments ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE deployments ALTER COLUMN id DROP DEFAULT")
