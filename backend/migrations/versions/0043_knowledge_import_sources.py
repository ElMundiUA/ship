"""knowledge import sources.

Revision ID: 0043_knowledge_import_sources
Revises: 0042_seed_bundle_version_string
Create Date: 2026-04-27
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0043_knowledge_import_sources"
down_revision: Union[str, None] = "0042_seed_bundle_version_string"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_import_sources",
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
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
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
        sa.Column("sync_cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('notion', 'confluence', 'static_upload', 'docs_repo', 'website')",
            name="ck_knowledge_import_sources_kind",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'syncing', 'error', 'disabled')",
            name="ck_knowledge_import_sources_status",
        ),
    )
    op.create_index(
        "ix_knowledge_import_sources_workspace_id",
        "knowledge_import_sources",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_import_sources_kind",
        "knowledge_import_sources",
        ["workspace_id", "kind"],
    )

    op.create_table(
        "knowledge_source_items",
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
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_import_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "item_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "source_id",
            "external_id",
            name="uq_knowledge_source_items_source_external",
        ),
    )
    op.create_index(
        "ix_knowledge_source_items_workspace_id",
        "knowledge_source_items",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_source_items_source_id",
        "knowledge_source_items",
        ["source_id"],
    )
    op.create_index(
        "ix_knowledge_source_items_fingerprint",
        "knowledge_source_items",
        ["content_fingerprint"],
    )

    op.create_table(
        "knowledge_ingestion_runs",
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
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_import_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error')",
            name="ck_knowledge_ingestion_runs_status",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_runs_workspace_id",
        "knowledge_ingestion_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_runs_source_id",
        "knowledge_ingestion_runs",
        ["source_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_runs_status",
        "knowledge_ingestion_runs",
        ["status"],
    )

    op.create_table(
        "bucket_article_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "article_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bucket_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_source_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'primary'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "article_id",
            "source_item_id",
            name="uq_bucket_article_sources_article_item",
        ),
    )
    op.create_index(
        "ix_bucket_article_sources_article_id",
        "bucket_article_sources",
        ["article_id"],
    )
    op.create_index(
        "ix_bucket_article_sources_source_item_id",
        "bucket_article_sources",
        ["source_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bucket_article_sources_source_item_id",
        table_name="bucket_article_sources",
    )
    op.drop_index(
        "ix_bucket_article_sources_article_id",
        table_name="bucket_article_sources",
    )
    op.drop_table("bucket_article_sources")
    op.drop_index(
        "ix_knowledge_ingestion_runs_status",
        table_name="knowledge_ingestion_runs",
    )
    op.drop_index(
        "ix_knowledge_ingestion_runs_source_id",
        table_name="knowledge_ingestion_runs",
    )
    op.drop_index(
        "ix_knowledge_ingestion_runs_workspace_id",
        table_name="knowledge_ingestion_runs",
    )
    op.drop_table("knowledge_ingestion_runs")
    op.drop_index(
        "ix_knowledge_source_items_fingerprint",
        table_name="knowledge_source_items",
    )
    op.drop_index(
        "ix_knowledge_source_items_source_id",
        table_name="knowledge_source_items",
    )
    op.drop_index(
        "ix_knowledge_source_items_workspace_id",
        table_name="knowledge_source_items",
    )
    op.drop_table("knowledge_source_items")
    op.drop_index(
        "ix_knowledge_import_sources_kind",
        table_name="knowledge_import_sources",
    )
    op.drop_index(
        "ix_knowledge_import_sources_workspace_id",
        table_name="knowledge_import_sources",
    )
    op.drop_table("knowledge_import_sources")
