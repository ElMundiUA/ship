"""workspace_policies + workspace_policy_exceptions (RFC-0008 §G).

Introduces two new tables backing the workspace-level Policy
primitive (PR-5): ``workspace_policies`` stores one row per
mirror-lane rule the platform team has declared, and
``workspace_policy_exceptions`` per-repo opt-outs from a given
policy. No existing rows need backfilling — a fresh workspace
starts with zero policies and nothing about the semantics of
existing ``Pipeline`` rows changes.

Revision ID: 0025_workspace_policies
Revises: 0024_fleet_requests
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0025_workspace_policies"
down_revision: Union[str, None] = "0024_fleet_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_policies",
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
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'mirror_lane'"),
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("pattern_id", sa.String(length=120), nullable=False),
        sa.Column("lane_id", sa.String(length=64), nullable=False),
        sa.Column("cadence", sa.String(length=120), nullable=False),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
            "lane_id",
            name="uq_workspace_policies_lane",
        ),
    )
    op.create_index(
        "ix_workspace_policies_workspace_id",
        "workspace_policies",
        ["workspace_id"],
    )

    op.create_table(
        "workspace_policy_exceptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspace_policies.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "workspace_repos.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=512), nullable=True),
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
            "policy_id",
            "repo_id",
            name="uq_workspace_policy_exceptions_repo",
        ),
    )
    op.create_index(
        "ix_workspace_policy_exceptions_policy_id",
        "workspace_policy_exceptions",
        ["policy_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_policy_exceptions_policy_id",
        table_name="workspace_policy_exceptions",
    )
    op.drop_table("workspace_policy_exceptions")
    op.drop_index(
        "ix_workspace_policies_workspace_id",
        table_name="workspace_policies",
    )
    op.drop_table("workspace_policies")
