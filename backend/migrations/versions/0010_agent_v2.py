"""agent v2 — knowledge buckets, bucket summaries, kb chunks, artifact feedback (C12)

Revision ID: 0010_agent_v2
Revises: 0009_agent_surface
Create Date: 2026-04-20

C12 "real agent" layer on top of the existing agent-surface tables:

- ``knowledge_buckets`` — user-named thematic buckets (e.g. ``auth-
  refactor``). Conversations that close out get summarised into a
  bucket; a future conversation that touches the same topic retrieves
  the bucket summary as warmed context.
- ``bucket_summaries`` — one row per packed conversation, rolled up
  under a bucket. Carries the natural-language summary + its embedding
  so topic retrieval is a vector similarity search.
- ``kb_chunks`` — ingested ``.ship/knowledge/**/*.md`` chunks, one row
  per chunk, with pgvector embedding + content SHA for idempotent re-
  indexing.
- ``artifact_feedback`` — asynchronous feedback the agent (or a user)
  files against a specific artifact id (`pattern/cloud-base`, etc.).
  Separate from ``improvements`` because improvements are proposed
  *changes* to the tenant's repo while feedback is about the *Ship
  catalog* itself (or a repo artifact).

We extend ``chat_threads`` with three columns for the single-window UX:

- ``topic_summary`` — short natural-language recap of the thread so the
  sentinel "Packed → bucket X" UI has something to render.
- ``packed_into_bucket_id`` — FK back to the bucket the thread rolled
  into (nullable because an archived thread without a packed summary
  is still legal).
- ``last_user_activity_at`` — bumped on every user message; used by
  the "active thread" resolver to pick the freshest live conversation.

All new tables carry workspace cascades so B6 disconnect wipes them
cleanly.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_agent_v2"
down_revision: Union[str, None] = "0009_agent_surface"
branch_labels = None
depends_on = None


# ``text-embedding-3-small`` emits 1536-dimensional vectors. Locking the
# migration to the small model's dimension keeps the schema stable; a
# future bump to -large (3072) would land as a fresh migration + re-
# embed pass.
_EMBED_DIM = 1536


def upgrade() -> None:
    # The ``vector`` extension was already enabled by the initial
    # migration; re-asserting it is a no-op and keeps this migration
    # replay-safe on a brand-new DB that somehow skipped 0001.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- knowledge_buckets --------------------------------------------------
    op.create_table(
        "knowledge_buckets",
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
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # ``vector(n)`` isn't in sqlalchemy core — fall back to
        # raw SQL. We drop this column to nullable so buckets can
        # exist before any summary is embedded against them.
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),  # placeholder; swapped below
            nullable=True,
        ),
        sa.Column(
            "archived_at", sa.DateTime(timezone=True), nullable=True
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
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_knowledge_buckets_workspace_slug"
        ),
    )
    # Swap the placeholder array column for a real ``vector(1536)``
    # column. Alembic's core types don't know about pgvector, and
    # committing a third-party SQLAlchemy type to the migration would
    # force the (optional) pgvector Python package into the migration
    # runner's dependency graph. Raw SQL is the lowest-friction path.
    op.execute(
        "ALTER TABLE knowledge_buckets DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )
    op.create_index(
        "ix_knowledge_buckets_workspace_id",
        "knowledge_buckets",
        ["workspace_id"],
    )

    # --- bucket_summaries ---------------------------------------------------
    op.create_table(
        "bucket_summaries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),  # placeholder, swapped below
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "ALTER TABLE bucket_summaries DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )
    op.create_index(
        "ix_bucket_summaries_bucket_id",
        "bucket_summaries",
        ["bucket_id"],
    )
    # HNSW index for fast cosine similarity retrieval. ``m=16``,
    # ``ef_construction=64`` are pgvector's documented defaults and
    # match our expected < 10k-summary scale.
    op.execute(
        "CREATE INDEX ix_bucket_summaries_embedding "
        "ON bucket_summaries USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # --- kb_chunks ----------------------------------------------------------
    op.create_table(
        "kb_chunks",
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
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Chunks of the same file are indexed by integer offset so a
        # re-index pass can diff (path, idx) against the current SHA.
        sa.Column(
            "chunk_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("content_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),
            nullable=True,
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "repo_id",
            "source_path",
            "chunk_index",
            "content_sha",
            name="uq_kb_chunks_repo_path_idx_sha",
        ),
    )
    op.execute(
        "ALTER TABLE kb_chunks DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )
    op.create_index(
        "ix_kb_chunks_workspace_id",
        "kb_chunks",
        ["workspace_id"],
    )
    op.create_index(
        "ix_kb_chunks_repo_source",
        "kb_chunks",
        ["repo_id", "source_path"],
    )
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding "
        "ON kb_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # --- artifact_feedback --------------------------------------------------
    op.create_table(
        "artifact_feedback",
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
        sa.Column("artifact_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "linked_pr_url", sa.String(length=1024), nullable=True
        ),
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        "ix_artifact_feedback_workspace_id",
        "artifact_feedback",
        ["workspace_id"],
    )
    op.create_index(
        "ix_artifact_feedback_artifact_id",
        "artifact_feedback",
        ["artifact_id"],
    )

    # --- chat_threads extensions --------------------------------------------
    op.add_column(
        "chat_threads",
        sa.Column("topic_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "chat_threads",
        sa.Column(
            "packed_into_bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_buckets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_threads",
        sa.Column(
            "last_user_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_chat_threads_last_user_activity",
        "chat_threads",
        ["workspace_id", "created_by_user_id", "last_user_activity_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_threads_last_user_activity", table_name="chat_threads"
    )
    op.drop_column("chat_threads", "last_user_activity_at")
    op.drop_column("chat_threads", "packed_into_bucket_id")
    op.drop_column("chat_threads", "topic_summary")

    op.drop_index(
        "ix_artifact_feedback_artifact_id", table_name="artifact_feedback"
    )
    op.drop_index(
        "ix_artifact_feedback_workspace_id", table_name="artifact_feedback"
    )
    op.drop_table("artifact_feedback")

    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding")
    op.drop_index("ix_kb_chunks_repo_source", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_workspace_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")

    op.execute("DROP INDEX IF EXISTS ix_bucket_summaries_embedding")
    op.drop_index(
        "ix_bucket_summaries_bucket_id", table_name="bucket_summaries"
    )
    op.drop_table("bucket_summaries")

    op.drop_index(
        "ix_knowledge_buckets_workspace_id", table_name="knowledge_buckets"
    )
    op.drop_table("knowledge_buckets")
