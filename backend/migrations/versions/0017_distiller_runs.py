"""distiller_runs: queue table for the Phase 6 Distiller

Revision ID: 0017_distiller_runs
Revises: 0016_backfill_summary_articles
Create Date: 2026-04-21

Phase 6a ships the synchronous ingest path (``POST
/v1/workspaces/{ws}/buckets/{slug}/distill``) as a stub — the run
row is created and transitioned to ``done`` inside the request
handler, and a new :class:`BucketArticle` is inserted based on a
tiny rule-set (slug collides with an existing published row →
``update``, non-empty body → ``new``, empty body → ``skip``).

We still persist the row even for the happy path because:

- it's the audit trail ("what did the Distiller ingest against
  this bucket, and when?") regardless of whether Phase 6b's LLM
  classifier exists yet;
- the shape is stable enough to drive a console history panel
  today without schema churn when the real queue lands;
- backfilling decisions against a freshly-enabled queue is
  simpler when the rows already exist.

Design
------

- ``workspace_id`` + ``bucket_id`` are both hard FKs (with ``ON
  DELETE CASCADE``) so deleting the parent cleans up the history.
  This matches ``bucket_summaries`` / ``bucket_articles``.
- ``status`` / ``decision`` are ``String`` columns with CHECK
  constraints instead of PG enums so Phase 6b can extend the
  vocabulary without a schema-touching migration. The allowed
  values live alongside their :class:`DistillerRunStatus` /
  :class:`DistillerRunDecision` string-constants classes.
- ``input_ref`` / ``output_refs`` are opaque JSONB blobs — the
  Phase 6a stub writes a minimal payload (``title_hint``,
  ``slug_hint``, ``bytes`` on the input; ``article_ids``, ``diff``
  on the output) and downstream can layer more fields in without
  re-versioning the schema.
- Indexes on ``workspace_id`` / ``bucket_id`` / ``status``. These
  are the three fields the listing endpoint orders/filters by.
- ``created_by_user_id`` is NULLable so a run initiated by a
  pipeline/system token still lands cleanly.

Downgrade drops the table and nothing else; ``bucket_articles``
rows created by stub runs stay where they are — we can't tell
"was this article created by the Distiller?" from the article
row alone, and that's fine. Provenance on the article carries
enough context for an operator to reason about it.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0017_distiller_runs"
down_revision: Union[str, None] = "0016_backfill_summary_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "distiller_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column(
            "input_ref",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_refs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
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
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_distiller_runs_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL "
            "OR decision IN ('new', 'update', 'skip', 'error')",
            name="ck_distiller_runs_decision",
        ),
    )
    op.create_index(
        "ix_distiller_runs_workspace_id",
        "distiller_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_distiller_runs_bucket_id",
        "distiller_runs",
        ["bucket_id"],
    )
    op.create_index(
        "ix_distiller_runs_status",
        "distiller_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_distiller_runs_status", table_name="distiller_runs")
    op.drop_index("ix_distiller_runs_bucket_id", table_name="distiller_runs")
    op.drop_index(
        "ix_distiller_runs_workspace_id", table_name="distiller_runs"
    )
    op.drop_table("distiller_runs")
