"""drop Pipeline C — knowledge_promotion_candidates + bucket_articles.overrides_workspace_article_id

Retires the canonical/promotion/dedup pipeline. The harvester →
router → synthesiser pipeline is the single sanctioned write path for
workspace knowledge; cross-scope overrides and dedup-cluster cache
have no role in that flow.

Drops:
- ``knowledge_promotion_candidates`` table (and its indexes/constraints).
- ``bucket_articles.overrides_workspace_article_id`` column (and its
  indexes / FKs).

Revision ID: 0052_drop_pipeline_c
Revises: 0051_agent_roles
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0052_drop_pipeline_c"
down_revision: Union[str, None] = "0051_agent_roles"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_knowledge_promotion_candidates_ws_ttl",
        table_name="knowledge_promotion_candidates",
    )
    op.drop_table("knowledge_promotion_candidates")

    op.drop_index(
        "ix_bucket_articles_overrides_ws",
        table_name="bucket_articles",
    )
    op.drop_constraint(
        "fk_bucket_articles_overrides_ws",
        "bucket_articles",
        type_="foreignkey",
    )
    op.drop_column("bucket_articles", "overrides_workspace_article_id")


def downgrade() -> None:
    op.add_column(
        "bucket_articles",
        sa.Column(
            "overrides_workspace_article_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_bucket_articles_overrides_ws",
        "bucket_articles",
        "bucket_articles",
        ["overrides_workspace_article_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bucket_articles_overrides_ws",
        "bucket_articles",
        ["overrides_workspace_article_id"],
        postgresql_where=sa.text("overrides_workspace_article_id IS NOT NULL"),
    )

    op.create_table(
        "knowledge_promotion_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("article_ids", postgresql.JSONB(), nullable=False),
        sa.Column("slug_hint", sa.Text(), nullable=False),
        sa.Column("centroid_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="uq_knowledge_promotion_candidates_fp",
        ),
    )
    op.create_index(
        "ix_knowledge_promotion_candidates_ws_ttl",
        "knowledge_promotion_candidates",
        ["workspace_id", "ttl_expires_at"],
    )
