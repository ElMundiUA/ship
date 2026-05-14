"""E17 — navigator_memories storage for the mem0-backed Navigator memory.

Stands up the table mem0 mirrors into and our service layer reads
from. mem0 has its own internal storage too (configured to point at
the same Postgres+pgvector); the mirror table here is what we query
when we need access-control filtering, source-message provenance,
or the Console ``/memory`` page's list view — those are
Ship-specific concerns mem0's own schema doesn't model.

Schema decisions:

- ``owner_user_id`` is the access-control axis. Workspace admins do
  NOT see another user's facts; every read goes through
  ``WHERE owner_user_id = current_user.id`` plus the workspace
  scope. One user across multiple workspaces gets separate fact
  sets per workspace.
- ``mem0_id`` mirrors mem0's own UUID so the two halves stay
  reconcilable. Unique index keeps the mirror at 1:1.
- ``source_message_id`` + ``source_message_position`` implement the
  variant-V provenance contract: one fact = one source user message.
  The position lets the Console pull ±5 surrounding messages
  without a JOIN against a re-sortable list.
- ``embedding vector(1536)`` — text-embedding-3-small native dim.
- ``intent_at_capture`` tags facts extracted during
  ``intent='shape_project'`` so the retrieval boost in ELS-128 can
  surface drafting-time context when the user reopens the project.

Indices:

- ``ivfflat`` on ``embedding`` for the search hot path.
- ``(owner_user_id, workspace_id)`` for the access-controlled list.
- ``source_thread_id`` for "show all facts from this thread" reverse
  lookup (Console UI).

Revision ID: 0070_navigator_memories
Revises: 0069_agent_dispatch_locks
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0070_navigator_memories"
down_revision: Union[str, None] = "0069_agent_dispatch_locks"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "navigator_memories",
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
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        # Provenance pointers. ``ondelete='SET NULL'`` so deleting a
        # chat thread / message doesn't cascade-wipe the extracted
        # facts — the facts outlive their source, the pointer just
        # goes stale.
        sa.Column(
            "source_thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Position of the source message within the thread — the
        # Console UI uses this to pull ±5 surrounding messages
        # without re-sorting the full thread list.
        sa.Column("source_message_position", sa.Integer(), nullable=True),
        # Project tag for retrieval boost (ELS-128). Free-form string
        # because trackers spell project ids differently (Linear UUID,
        # Notion page id, GitHub repo full-name) and we shouldn't
        # FK across tracker boundaries.
        sa.Column(
            "project_native_id",
            sa.String(255),
            nullable=True,
        ),
        # ``shape_project`` etc — kept so we can boost drafting-time
        # facts when the same user reopens that project later.
        sa.Column(
            "intent_at_capture",
            sa.String(64),
            nullable=True,
        ),
        # mem0's own UUID — keeps mirror reconcilable.
        sa.Column("mem0_id", sa.String(64), nullable=False),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
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

    # 1:1 with mem0's own row.
    op.create_index(
        "uq_navigator_memories_mem0_id",
        "navigator_memories",
        ["mem0_id"],
        unique=True,
    )
    # Access-controlled list view (Console ``/memory``).
    op.create_index(
        "ix_navigator_memories_owner_workspace",
        "navigator_memories",
        ["owner_user_id", "workspace_id"],
    )
    # "Show all facts from this thread" reverse lookup.
    op.create_index(
        "ix_navigator_memories_source_thread",
        "navigator_memories",
        ["source_thread_id"],
    )
    # Project-scope retrieval boost (ELS-128).
    op.create_index(
        "ix_navigator_memories_project",
        "navigator_memories",
        ["owner_user_id", "workspace_id", "project_native_id"],
    )
    # Vector search index — ivfflat with cosine. ``lists=100`` is the
    # rule-of-thumb starting point; we'll re-tune once we observe row
    # counts in prod.
    op.execute(
        "CREATE INDEX ix_navigator_memories_embedding "
        "ON navigator_memories USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_navigator_memories_embedding")
    op.drop_index(
        "ix_navigator_memories_project",
        table_name="navigator_memories",
    )
    op.drop_index(
        "ix_navigator_memories_source_thread",
        table_name="navigator_memories",
    )
    op.drop_index(
        "ix_navigator_memories_owner_workspace",
        table_name="navigator_memories",
    )
    op.drop_index(
        "uq_navigator_memories_mem0_id",
        table_name="navigator_memories",
    )
    op.drop_table("navigator_memories")
