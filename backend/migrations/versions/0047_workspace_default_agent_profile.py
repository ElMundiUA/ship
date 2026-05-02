"""workspace.default_agent_profile: workspace-level default for per-state agent profile.

Revision ID: 0047_ws_default_agent
Revises: 0046_waitlist_submissions
Create Date: 2026-05-02

Per-state ``default_agent_profile`` in process configs has always
defaulted to the literal string ``"auto"`` — a runtime sentinel that
asks the orchestrator to pick. The new ``/process`` editor needs a
real workspace-level default so it can:

  - resolve ``"auto"`` to a concrete profile when projecting the
    process for the editor (so operators see what will actually run),
  - hard-stop edits to a process when the workspace hasn't picked a
    default yet (gating banner blocks the editor surface).

``default_agent_profile`` is nullable on insert; the gating logic
treats NULL as "operator must pick one before editing." Existing
workspaces backfill to NULL on purpose so the gate fires for them
too — choosing the default is a deliberate decision, not something
to silently inherit from a guess.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0047_ws_default_agent"
down_revision: Union[str, None] = "0046_waitlist_submissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "default_agent_profile",
            sa.String(64),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "default_agent_profile")
