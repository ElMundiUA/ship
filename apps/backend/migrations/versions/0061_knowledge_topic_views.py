"""knowledge_topic_view: cached canonical markdown per topic_tag

The topic view is a derived surface — a coherent markdown article
rendered from the active claims that share a topic_tag. It replaces
the role of ``BucketArticle`` as the retrieval target for agent /
operator search: instead of synth deciding "new vs update vs skip"
and producing one blob per bucket per tick, the renderer takes the
**current** active claims for a topic and produces one canonical
view, regenerating only when the claim set actually changes.

Schema notes:

- ``topic_tag`` is the same free-form kebab-case label the extractor
  attaches to claims via ``KnowledgeClaim.topic_tags``. Multi-tag
  claims appear in multiple topic views — that's the point.
- ``claim_set_sha`` is a deterministic hash of the sorted active
  claim ids feeding the view. Cache invalidation: if the cron tick
  computes a different sha for the topic, the view gets regenerated;
  otherwise the LLM call is skipped.
- ``claim_count`` is denormalised so the read API can rank topics
  by canon depth without joining to ``knowledge_claim``.
- ``embedding`` is a pgvector(1536) so the topic view participates
  in the same cosine-search surface as ``bucket_articles`` /
  ``knowledge_claim``. Optional (NULL until embedded).

Uniqueness: ``(workspace_id, topic_tag)`` — at most one current view
per topic. Re-rendering an existing topic UPDATEs in place; we don't
keep historical views in this table (the supersedes graph already
records the underlying claim history).

Revision ID: 0061_knowledge_topic_views
Revises: 0060_claim_reconciled_at
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0061_knowledge_topic_views"
down_revision: Union[str, None] = "0060_claim_reconciled_at"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_EMBED_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_topic_view",
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
        sa.Column("topic_tag", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("claim_set_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "claim_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rendered_by_model",
            sa.String(length=64),
            nullable=True,
        ),
        # ARRAY first, ALTER below to pgvector — same idiom we already
        # use for knowledge_claim.embedding so the migration stays
        # within sqlalchemy's portable surface for the create_table
        # call.
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),
            nullable=True,
        ),
        sa.Column(
            "last_rendered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
            "workspace_id",
            "topic_tag",
            name="uq_knowledge_topic_view_ws_tag",
        ),
    )

    op.execute(
        "ALTER TABLE knowledge_topic_view DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )

    op.create_index(
        "ix_knowledge_topic_view_workspace_id",
        "knowledge_topic_view",
        ["workspace_id"],
    )
    op.execute(
        "CREATE INDEX ix_knowledge_topic_view_embedding "
        "ON knowledge_topic_view USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_topic_view_embedding")
    op.drop_index(
        "ix_knowledge_topic_view_workspace_id",
        table_name="knowledge_topic_view",
    )
    op.drop_table("knowledge_topic_view")
