"""agent surface tables (C8 improvements, C9 clarifications, C10 chat)

Revision ID: 0009_agent_surface
Revises: 0008_workspace_invites
Create Date: 2026-04-20

Adds four tables that back the human-in-the-loop side of Ship:

- ``clarifications`` — agent-raised questions about tickets/repos,
  with answer / skip / stale transitions.
- ``improvements`` — agent-proposed changes awaiting a yes / no /
  later decision.
- ``chat_threads`` — conversational context with the Ship agent.
- ``chat_messages`` — append-only messages inside a thread.

All four are workspace-scoped. ``repo_id`` cascades so B6's disconnect
wipes them automatically; ``pipeline_run_id`` uses SET NULL so we
keep the historical record even after a run ages out.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_agent_surface"
down_revision: Union[str, None] = "0008_workspace_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- clarifications -----------------------------------------------------
    op.create_table(
        "clarifications",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ticket_ref", sa.String(length=255), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "context",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "answered_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_clarifications_workspace_status",
        "clarifications",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_clarifications_repo_id", "clarifications", ["repo_id"]
    )

    # --- improvements -------------------------------------------------------
    op.create_table(
        "improvements",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "pipeline_run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(length=32), nullable=True),
        sa.Column("effort", sa.String(length=32), nullable=True),
        sa.Column(
            "context",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_action_url", sa.String(length=1024), nullable=True
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
    )
    op.create_index(
        "ix_improvements_workspace_decision",
        "improvements",
        ["workspace_id", "decision"],
    )
    op.create_index(
        "ix_improvements_repo_id", "improvements", ["repo_id"]
    )

    # --- chat_threads -------------------------------------------------------
    op.create_table(
        "chat_threads",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("workflow_id", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "resolved_ticket_ref", sa.String(length=255), nullable=True
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
    )
    op.create_index(
        "ix_chat_threads_workspace_id", "chat_threads", ["workspace_id"]
    )
    op.create_index("ix_chat_threads_repo_id", "chat_threads", ["repo_id"])

    # --- chat_messages ------------------------------------------------------
    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "author_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_messages_thread_created",
        "chat_messages",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_messages_thread_created", table_name="chat_messages"
    )
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_threads_repo_id", table_name="chat_threads")
    op.drop_index("ix_chat_threads_workspace_id", table_name="chat_threads")
    op.drop_table("chat_threads")
    op.drop_index("ix_improvements_repo_id", table_name="improvements")
    op.drop_index(
        "ix_improvements_workspace_decision", table_name="improvements"
    )
    op.drop_table("improvements")
    op.drop_index("ix_clarifications_repo_id", table_name="clarifications")
    op.drop_index(
        "ix_clarifications_workspace_status", table_name="clarifications"
    )
    op.drop_table("clarifications")
