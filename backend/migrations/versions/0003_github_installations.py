"""create github_installations for the GitHub App pilot flow

Revision ID: 0003_github_installations
Revises: 0002_users_external_subject
Create Date: 2026-04-19

The GitHub App "Ship" stores one row per installation linked to a Ship
workspace. ``installation_id`` is unique because the same App install can
only ever map to a single workspace at a time — re-installing into another
workspace updates the existing row instead of duplicating.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_github_installations"
down_revision: Union[str, None] = "0002_users_external_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=True),
        sa.Column("account_login", sa.String(length=120), nullable=True),
        sa.Column("account_type", sa.String(length=32), nullable=True),
        sa.Column("repository_selection", sa.String(length=16), nullable=True),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "installation_id", name="uq_github_installations_installation_id"
        ),
    )
    op.create_index(
        "ix_github_installations_workspace_id",
        "github_installations",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_installations_workspace_id",
        table_name="github_installations",
    )
    op.drop_table("github_installations")
