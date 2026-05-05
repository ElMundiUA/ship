"""workspace project priorities — state column

Adds the ``state`` axis to the prioritizer so a project can sit on the
dashboard in one of three buckets:

- ``active``   — agent may pick. Default for backfill.
- ``planning`` — operator is shaping it (brief, scope, anchor). The
  agent must NOT pick from this state.
- ``parked``   — explicitly not now. Dim, agent-invisible.

Backfill: every existing row is ``active`` (matches today's behaviour
where a saved row = pickable). The CHECK constraint pins the enum at
the DB layer so a typo'd PATCH can't smuggle in a fourth value.

Revision ID: 0057_priority_state
Revises: 0056_dashboard_priorities
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0057_priority_state"
down_revision: Union[str, None] = "0056_dashboard_priorities"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_TABLE = "workspace_project_priorities"
_CHECK = "ck_workspace_project_priorities_state"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "state IN ('active', 'planning', 'parked')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "state")
