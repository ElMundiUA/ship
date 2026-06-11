"""workspaces.autonomy — per-workspace agent autonomy profile (thesis 7)

Adds ``Workspace.autonomy`` (``high`` / ``balanced`` / ``conservative``)
— the dial for how much the *agent* may do on its own (skip approvals,
self-merge, self-pick work). It deliberately does NOT touch the control
plane: no lease / cap / cascade / idempotency constant or table changes
ride along, ever (headless-but-stateful invariant).

All existing rows backfill to ``balanced`` (the beta target) via the
server default; ``high`` is opt-in everywhere — no workspace (including
ElMundi) is seeded to it here. A CHECK constraint pins the enum at the
DB layer; :mod:`backend.app.services.agent_provider_resolver` mirrors
the same set.

Revision ID: 0086_workspace_autonomy
Revises: 0085_repo_deploy_planner_pref
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa

from alembic import op


revision: str = "0086_workspace_autonomy"
down_revision: Union[str, None] = "0085_repo_deploy_planner_pref"
branch_labels = None
depends_on = None


_TABLE = "workspaces"
_COLUMN = "autonomy"
_CHECK = "ck_workspaces_autonomy_enum"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'balanced'"),
        ),
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        f"{_COLUMN} IN ('high', 'balanced', 'conservative')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
