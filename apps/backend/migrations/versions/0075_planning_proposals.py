"""planning_proposals — Navigator mass-planning intake (ELS-170).

Persists the draft project + epics + deps proposal between extraction
(M1) and commit (M2). Operator edits via PATCH; commit flips
``committed_at`` and records the created ticket refs.

Revision ID: 0075_planning_proposals
Revises: 0074_inbox_taxonomy_v2
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0075_planning_proposals"
down_revision: Union[str, None] = "0074_inbox_taxonomy_v2"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "planning_proposals",
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
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pdf'"),
        ),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_by",
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
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "committed_ticket_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_planning_proposals_workspace",
        "planning_proposals",
        ["workspace_id"],
    )
    op.create_index(
        "ix_planning_proposals_thread",
        "planning_proposals",
        ["thread_id"],
        postgresql_where=sa.text("thread_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_proposals_thread", table_name="planning_proposals"
    )
    op.drop_index(
        "ix_planning_proposals_workspace", table_name="planning_proposals"
    )
    op.drop_table("planning_proposals")
