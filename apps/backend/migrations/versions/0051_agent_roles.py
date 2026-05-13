"""agent_roles — workspace-scoped CRUD for agent role prompts (Phase 2.4 Step A).

Ship-level defaults live as files under ``backend/app/resources/agent_roles/``
and stay read-only at runtime. This table holds workspace-scoped rows for
two cases:

* **Override** — slug equals a Ship default slug; the row's prompt
  shadows the default for the owning workspace.
* **Clone** — slug is unique within the workspace and ``base_role_slug``
  records the Ship default the clone was seeded from (informational, no
  FK because Ship defaults are file-backed).

The runtime resolver (``GET /v1/workspaces/{ws}/agent-roles/{slug}/resolve``)
prefers the workspace row when present, otherwise falls back to the file.

Revision ID: 0051_agent_roles
Revises: 0050_seed_default_policies
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0051_agent_roles"
down_revision: Union[str, None] = "0050_seed_default_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_roles",
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
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Informational pointer to the Ship default the row was cloned
        # from. NULL when the row is a same-slug override of a Ship
        # default, or a brand-new role with no parent.
        sa.Column("base_role_slug", sa.String(length=64), nullable=True),
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
            "workspace_id", "slug", name="uq_agent_roles_workspace_slug"
        ),
    )
    op.create_index(
        "ix_agent_roles_workspace_id",
        "agent_roles",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_roles_workspace_id", table_name="agent_roles")
    op.drop_table("agent_roles")
