"""knowledge_promotion_candidates — dedup cache table (RFC-0008 §I, PR-7B).

Backs ``GET /v1/workspaces/{ws}/knowledge/candidates`` and the
on-demand clustering pipeline in
:mod:`backend.app.services.knowledge_dedup`. The dedup clusters are
recomputed lazily; this table is the TTL cache so two operators
clicking "Promote candidates" within a few seconds don't both pay
the pairwise-similarity scan.

Design choices:

- ``article_ids`` is a JSONB list of ``bucket_articles.id`` strings,
  ordered deterministically so ``fingerprint`` is stable across
  invocations. We could have gone with a separate child table
  (``knowledge_promotion_candidate_articles``) but the membership
  is cold-read only + always loaded in bulk for the UI, so inlining
  it keeps the read path to a single index scan.
- Unique on ``(workspace_id, fingerprint)`` makes ``rebuild_candidates``
  idempotent — repeated refreshes upsert rather than duplicate.
- ``centroid_score`` is a float (mean pairwise cosine similarity
  inside the cluster) used only for UX sorting; it lives on the
  cluster row instead of being recomputed per-request because the
  clustering pass already has it in hand.

Revision ID: 0028_knowledge_promotion_candidates
Revises: 0027_knowledge_overrides
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0028_promotion_candidates"
down_revision: Union[str, None] = "0027_knowledge_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_promotion_candidates",
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
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "article_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("slug_hint", sa.Text(), nullable=False),
        sa.Column(
            "centroid_score",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "ttl_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
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


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_promotion_candidates_ws_ttl",
        table_name="knowledge_promotion_candidates",
    )
    op.drop_table("knowledge_promotion_candidates")
