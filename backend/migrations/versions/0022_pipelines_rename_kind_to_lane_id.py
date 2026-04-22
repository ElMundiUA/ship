"""pipelines — rename ``kind`` column to ``lane_id`` (RFC-0008 C3.4).

Revision ID: 0022_pipelines_rename_kind
Revises: 0021_agent_requests
Create Date: 2026-04-22

Post-RFC-0008 the seeded lane identifier is a stable *lane slug*
(``pr_review``, ``daily_standup``, …) rather than a "kind of
pipeline". Aligning the column name with :class:`Lane.lane_id` (same
concept, materialised in a different table) lets the two surfaces be
reasoned about as a single vocabulary going forward.

The rename is byte-for-byte safe — same type, same nullability, same
values. We also rename the ``(workspace_id, kind)`` unique constraint
so the Postgres catalog stays navigable after the swap.
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0022_pipelines_rename_kind"
down_revision: Union[str, None] = "0021_agent_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("pipelines", "kind", new_column_name="lane_id")
    op.execute(
        "ALTER TABLE pipelines "
        "RENAME CONSTRAINT uq_pipelines_workspace_kind "
        "TO uq_pipelines_workspace_lane_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pipelines "
        "RENAME CONSTRAINT uq_pipelines_workspace_lane_id "
        "TO uq_pipelines_workspace_kind"
    )
    op.alter_column("pipelines", "lane_id", new_column_name="kind")
