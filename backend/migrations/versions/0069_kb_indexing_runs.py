"""kb_indexing_runs — persisted per-run record for ``.ship/knowledge`` re-embeds (ELS-62)

Adds the ``kb_indexing_runs`` table so the Navigator's
``trigger_repo_kb_indexing`` / ``probe_repo_kb_indexing`` tools (and the
push-webhook reindex path) write to one observability surface. Shape
mirrors ``knowledge_ingestion_runs`` — pending / running / done / error
plus a JSONB ``stats`` blob the indexer's :class:`IndexReport` round-
trips into.

Reversible: ``downgrade`` drops the table. No backfill — push-webhook
reindexes prior to this migration were not recorded anywhere; existing
``kb_chunks`` rows are untouched.

Revision ID: 0069_kb_indexing_runs
Revises: 0068_routine_runs_fk_hotfix
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0069_kb_indexing_runs"
down_revision: Union[str, None] = "0068_routine_runs_fk_hotfix"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_indexing_runs",
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
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("trigger", sa.String(length=16), nullable=False),
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
            name="ck_kb_indexing_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('agent', 'push', 'manual')",
            name="ck_kb_indexing_runs_trigger",
        ),
    )
    op.create_index(
        "ix_kb_indexing_runs_repo_created",
        "kb_indexing_runs",
        ["repo_id", "created_at"],
    )
    op.create_index(
        "ix_kb_indexing_runs_workspace",
        "kb_indexing_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_kb_indexing_runs_active",
        "kb_indexing_runs",
        ["repo_id"],
        postgresql_where=sa.text("status IN ('pending','running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kb_indexing_runs_active",
        table_name="kb_indexing_runs",
    )
    op.drop_index(
        "ix_kb_indexing_runs_workspace",
        table_name="kb_indexing_runs",
    )
    op.drop_index(
        "ix_kb_indexing_runs_repo_created",
        table_name="kb_indexing_runs",
    )
    op.drop_table("kb_indexing_runs")
