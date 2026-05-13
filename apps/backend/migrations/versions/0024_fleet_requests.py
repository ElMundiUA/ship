"""fleet_requests — workspace-level fan-out parent (RFC-0008 D).

Introduces a new ``fleet_requests`` table and a nullable
``agent_requests.fleet_request_id`` FK so workspace-level "fan this
pattern out across N repos" dispatches have a single parent row.

The parent captures the frozen ``{pattern_id, inputs, agent_slug,
context_ref}`` payload once; the fan-out creates one child
:class:`AgentRequest` per repo with ``fleet_request_id`` pointing
back. Child validation is **best-effort** (RFC-0008 §D): repos that
fail pre-dispatch checks land on the API response's ``rejections``
list without blocking the rest of the fan-out.

Legacy per-repo dispatches keep working untouched: ``fleet_request_id``
is nullable and no existing row needs a backfill.

Revision ID: 0024_fleet_requests
Revises: 0023_agent_requests_pattern
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0024_fleet_requests"
down_revision: Union[str, None] = "0023_agent_requests_pattern"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_requests",
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
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("pattern_id", sa.String(length=120), nullable=True),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("context_ref", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'dispatching'"),
        ),
        sa.Column(
            "target_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rejections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
    )
    op.create_index(
        "ix_fleet_requests_workspace_created",
        "fleet_requests",
        ["workspace_id", "created_at"],
    )

    op.add_column(
        "agent_requests",
        sa.Column(
            "fleet_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_agent_requests_fleet_request_id",
        "agent_requests",
        "fleet_requests",
        ["fleet_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_requests_fleet_request_id",
        "agent_requests",
        ["fleet_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_requests_fleet_request_id", table_name="agent_requests"
    )
    op.drop_constraint(
        "fk_agent_requests_fleet_request_id",
        "agent_requests",
        type_="foreignkey",
    )
    op.drop_column("agent_requests", "fleet_request_id")

    op.drop_index(
        "ix_fleet_requests_workspace_created", table_name="fleet_requests"
    )
    op.drop_table("fleet_requests")
