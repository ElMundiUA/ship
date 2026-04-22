"""custom_patterns — workspace-private catalog layer (RFC-0008 §H).

Introduces a ``custom_patterns`` table so workspace admins can
author catalog patterns at runtime (via the Console's AI author
modal, Navigator, or hand-crafted JSON) without forking Ship. Each
row mirrors the subset of a baked-in pattern's frontmatter the
catalog adapter needs to synthesise a :class:`CatalogArtifact`.

Revision ID: 0026_custom_patterns
Revises: 0025_workspace_policies
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0026_custom_patterns"
down_revision: Union[str, None] = "0025_workspace_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_patterns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pattern_id", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column(
            "modes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
            "workspace_id",
            "pattern_id",
            name="uq_custom_patterns_pattern_id",
        ),
    )
    op.create_index(
        "ix_custom_patterns_workspace_id",
        "custom_patterns",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_custom_patterns_workspace_id",
        table_name="custom_patterns",
    )
    op.drop_table("custom_patterns")
