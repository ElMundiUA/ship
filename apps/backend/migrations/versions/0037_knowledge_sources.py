"""knowledge_sources — durable source state for knowledge buckets.

Revision ID: 0037_knowledge_sources
Revises: 0036_chat_threads_archived_at
Create Date: 2026-04-25
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0037_knowledge_sources"
down_revision: Union[str, None] = "0036_chat_threads_archived_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
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
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ready'"),
        ),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            (
                "kind IN ('repo_context', 'connector', 'git_docs', "
                "'static_upload', 'agent_memory', 'repo_files', "
                "'audio_transcript', 'promoted')"
            ),
            name="ck_knowledge_sources_kind",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'syncing', 'error', 'disabled')",
            name="ck_knowledge_sources_status",
        ),
    )
    op.create_index(
        "ix_knowledge_sources_workspace_id",
        "knowledge_sources",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_sources_bucket_id", "knowledge_sources", ["bucket_id"]
    )
    op.create_index(
        "ix_knowledge_sources_kind",
        "knowledge_sources",
        ["workspace_id", "kind"],
    )

    op.execute(
        """
        INSERT INTO knowledge_sources (
            workspace_id,
            bucket_id,
            kind,
            config,
            status,
            content_fingerprint,
            last_synced_at
        )
        SELECT
            workspace_id,
            id,
            CASE source_kind
                WHEN 'connector_proxy' THEN 'connector'
                WHEN 'external_static' THEN 'static_upload'
                ELSE source_kind
            END,
            COALESCE(source_ref, '{}'::jsonb),
            CASE
                WHEN archived_at IS NULL THEN 'ready'
                ELSE 'disabled'
            END,
            source_ref ->> 'content_sha',
            updated_at
        FROM knowledge_buckets
        """
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_sources_kind", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_bucket_id", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_workspace_id", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
