"""add users.external_subject for Auth0 / OIDC JIT mapping

Revision ID: 0002_users_external_subject
Revises: 0001_initial_tenancy
Create Date: 2026-04-19

The column is nullable so existing local-mode rows stay valid; uniqueness
is enforced via a partial-style unique constraint (Postgres allows multiple
NULLs in a UNIQUE column by default, which is exactly what we want here).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_users_external_subject"
down_revision: Union[str, None] = "0001_initial_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("external_subject", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_users_external_subject",
        "users",
        ["external_subject"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_external_subject", table_name="users")
    op.drop_column("users", "external_subject")
