"""knowledge_overrides — cross-scope override link on bucket_articles (PR-7A).

Introduces ``bucket_articles.overrides_workspace_article_id`` — a
self-referential FK that lets a repo-scope article explicitly declare
itself an override of a workspace-scope canonical article. Distinct
from ``supersedes_id`` which still captures intra-bucket version
history; this column spans scopes and is the foundation of the
workspace-knowledge resolver (7A) and the promote/adopt flows
(7B/7C).

Revision ID: 0027_knowledge_overrides
Revises: 0026_custom_patterns
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0027_knowledge_overrides"
down_revision: Union[str, None] = "0026_custom_patterns"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index(
        "ix_bucket_articles_overrides_ws",
        table_name="bucket_articles",
    )
    op.drop_constraint(
        "fk_bucket_articles_overrides_ws",
        "bucket_articles",
        type_="foreignkey",
    )
    op.drop_column(
        "bucket_articles",
        "overrides_workspace_article_id",
    )
