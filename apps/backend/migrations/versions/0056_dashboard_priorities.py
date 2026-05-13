"""workspace project priorities table

Backs the Dashboard v2 prioritizer (PR-1). One row per (workspace,
tracker project) carrying an explicit ``ordinal`` — 0 is highest. We
prefer an explicit column over ``created_at``-sort so reordering is
unambiguous and bulk replace on save is a single transaction.

``autonomy_paused`` lives in :class:`Workspace.settings` JSONB rather
than its own column so the dashboard can flip the workspace-level
kill switch without a schema migration round-trip.

Revision ID: 0056_dashboard_priorities
Revises: 0055_purge_legacy_routines
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0056_dashboard_priorities"
down_revision: Union[str, None] = "0055_purge_legacy_routines"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_project_priorities",
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
        sa.Column("project_native_id", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "project_native_id",
            name="uq_workspace_project_priorities_ws_native",
        ),
    )
    op.create_index(
        "ix_workspace_project_priorities_ws_ord",
        "workspace_project_priorities",
        ["workspace_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_project_priorities_ws_ord",
        table_name="workspace_project_priorities",
    )
    op.drop_table("workspace_project_priorities")
