"""workspaces.agent_provider — bound agent runtime per workspace

Adds a ``Workspace.agent_provider`` column (``cursor`` / ``codex`` /
``claude``) so the autonomous pipeline picks which local CLI to run on
the GHA runner the same way it picks a tracker. Default is ``cursor``
to keep existing workspaces working without an explicit operator
choice.

A CHECK constraint pins the accepted enum at the DB layer; the resolver
in :mod:`backend.app.services.agent_provider_resolver` mirrors the same
set so a typo on either side surfaces as a 422 / IntegrityError instead
of an opaque CLI invocation failure.

Revision ID: 0063_agent_provider
Revises: 0062_priority_default_planning
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0063_agent_provider"
down_revision: Union[str, None] = "0062_priority_default_planning"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_TABLE = "workspaces"
_COLUMN = "agent_provider"
_CHECK = "ck_workspaces_agent_provider_enum"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'cursor'"),
        ),
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        f"{_COLUMN} IN ('cursor', 'codex', 'claude')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
