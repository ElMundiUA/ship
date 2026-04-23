"""workspace_policies (prose standing rules — Workspace policy injection).

Brand-new table reusing the freed-up ``workspace_policies`` name
(the old mirror-lane primitive was renamed to ``fleet_lanes`` in
revision ``0029``). Holds free-text rules a workspace admin
declares once and Ship enforces by **injecting them into the
agent's system prompt at runtime** — both in the Navigator chat
(``TopicService.assemble_messages``) and in the ``shipctl run``
stdout that GitHub-Actions agents consume.

Schema:
- ``id UUID PK``
- ``workspace_id UUID FK → workspaces.id ON DELETE CASCADE``
- ``title VARCHAR(160) NOT NULL`` — short label ("Always work via PR")
- ``body TEXT NOT NULL`` — markdown body of the rule
- ``enabled BOOL NOT NULL DEFAULT true`` — toggle without delete
- ``sort_order INT NOT NULL DEFAULT 0`` — stable presentation order
- ``created_at`` / ``updated_at`` (timestamptz NOT NULL)

Indexed on ``(workspace_id, enabled, sort_order)`` so the
preamble-rendering query (filter to ``enabled=true``, sort by
``sort_order, created_at``) doesn't scan the whole table.

Revision ID: 0030_policies_prose
Revises: 0029_rename_to_fleet_lanes
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0030_policies_prose"
down_revision: Union[str, None] = "0029_rename_to_fleet_lanes"
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
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
        "ix_workspace_policies_workspace_enabled_sort",
        "workspace_policies",
        ["workspace_id", "enabled", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_policies_workspace_enabled_sort",
        table_name="workspace_policies",
    )
    op.drop_table("workspace_policies")
