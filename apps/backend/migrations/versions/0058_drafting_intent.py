"""chat thread intent + project originating thread (project-first delivery, ELS-74)

Two columns drop in for PR2 of the project-first delivery flow:

- ``chat_threads.intent`` — tags a thread with its conversation purpose.
  Today only ``shape_project`` is meaningful (the Navigator drafting
  mode); NULL means "default conversation," matching every existing
  thread. The system-prompt assembler injects a "Project drafting mode"
  block when this is set so the agent biases toward shaping a brief
  rather than calling ``create_project`` immediately.

- ``workspace_project_priorities.originating_thread_id`` — backref to
  the thread the PO drafted the project in. Lets the dashboard render
  a "Continue shaping" link on each Drafts row that re-opens the same
  Navigator thread instead of fragmenting the conversation. Nullable
  because projects predating this work (and projects created outside
  Navigator) won't have one.

Revision ID: 0058_drafting_intent
Revises: 0057_priority_state
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0058_drafting_intent"
down_revision: Union[str, None] = "0057_priority_state"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column("intent", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workspace_project_priorities",
        sa.Column(
            "originating_thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "workspace_project_priorities", "originating_thread_id"
    )
    op.drop_column("chat_threads", "intent")
