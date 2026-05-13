"""agent_requests — add ``pattern_id`` + ``inputs`` (RFC-0008 C4).

Revision ID: 0023_agent_requests_pattern
Revises: 0022_pipelines_rename_kind
Create Date: 2026-04-22

RFC-0008 C4 moves the Console's ``/requests`` surface from the
legacy ``{agent_slug, prompt}`` shape to a catalog-backed
``{pattern_id, inputs}`` dispatcher. ``pattern_id`` references a
row in the pattern catalog (``artifacts/patterns/<id>``); ``inputs``
is the structured form payload collected from
``pattern.spec.inputs``. Both columns are nullable/empty-defaulted
so legacy dispatches continue to round-trip through the same table.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0023_agent_requests_pattern"
down_revision: Union[str, None] = "0022_pipelines_rename_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_requests",
        sa.Column("pattern_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "agent_requests",
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_requests", "inputs")
    op.drop_column("agent_requests", "pattern_id")
