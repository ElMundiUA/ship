"""extend workspaces.agent_provider CHECK to allow 'ship' (thesis 6)

The self-spawn runtime (ELS-241) joins cursor/codex/claude at the DB
layer. Forward revision — 0063 is applied in prod and must not be
edited in place. ``ship`` stays internal/dogfood-gated above the DB:
the self-serve config scope doesn't offer it and the CLI dispatcher
refuses it without ``SHIP_ALLOW_SELF_SPAWN=true``.

Revision ID: 0087_agent_provider_ship
Revises: 0086_workspace_autonomy
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0087_agent_provider_ship"
down_revision: Union[str, None] = "0086_workspace_autonomy"
branch_labels = None
depends_on = None


_TABLE = "workspaces"
_CHECK = "ck_workspaces_agent_provider_enum"
_COLUMN = "agent_provider"


def upgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        f"{_COLUMN} IN ('cursor', 'codex', 'claude', 'ship')",
    )


def downgrade() -> None:
    # Restore the 3-value set. Any row already on 'ship' must be moved
    # back first or this constraint re-creation fails loudly — that is
    # deliberate (no silent data munging in a downgrade).
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        f"{_COLUMN} IN ('cursor', 'codex', 'claude')",
    )
