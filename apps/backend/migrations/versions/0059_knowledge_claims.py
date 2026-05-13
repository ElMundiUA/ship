"""knowledge_claim store + body_md on knowledge_source_items

The knowledge pipeline switches its unit of canonical truth from the
``BucketArticle`` (LLM-curated digest) to the **claim**: an atomic,
verifiable assertion extracted from a source document. Claims carry
their own embedding + provenance + supersedes chain, so reconciliation
(this fact replaces that one, this one is duplicate) happens at the
fact level instead of being baked into one all-or-nothing article body.

Articles aren't being deleted — they become a derived view rendered
on top of the active claim set per topic. This migration only sets
up the new tables; the extractor + reconciliation engine + view
renderer ride on later phases.

Schema:

- ``knowledge_claim`` — the canonical claim row.
  - ``status``: ``active`` (current canon) / ``superseded`` (replaced
    by another row via ``superseded_by_id``) / ``stale`` (no source
    has confirmed it for N decay-cron ticks) / ``disputed`` (operator
    review pending) / ``archived`` (operator-killed).
  - ``topic_tags`` is multi-tag because one claim about Linear FSM is
    legitimately both ``integrations`` and ``architecture-decisions``;
    the existing single-bucket model was forcing a false choice.
  - ``source_links`` JSONB carries the (source_item_id, excerpt,
    extracted_at) triples — one claim can be reinforced by multiple
    sources, and that's signal we want to surface in search results.
  - ``supersedes_id`` / ``superseded_by_id`` together build the
    history graph; a chain query gives "how this fact has changed
    over time".

- New columns on ``knowledge_source_items``:
  - ``body_md`` — the actual document body. Until now we kept it on
    ``Improvement.body``; that table was always meant as a queue of
    events, not a corpus. Lifting body to source items lets the
    extractor run idempotently keyed on ``body_md_sha``.
  - ``body_md_sha`` — sha256 of the body, used by the extractor to
    skip un-changed items.
  - ``extracted_at`` — when the extractor last ran. NULL = pending.

Revision ID: 0059_knowledge_claims
Revises: 0058_drafting_intent
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0059_knowledge_claims"
down_revision: Union[str, None] = "0058_drafting_intent"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_EMBED_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- knowledge_claim --------------------------------------------------
    op.create_table(
        "knowledge_claim",
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
        sa.Column("claim_md", sa.Text(), nullable=False),
        sa.Column(
            "claim_md_sha",
            sa.String(length=64),
            nullable=False,
        ),
        # ARRAY first; ALTER below promotes to pgvector(1536). Same
        # pattern methodology_chunks uses — keeps the create_table call
        # within sqlalchemy's portable surface.
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),
            nullable=True,
        ),
        sa.Column(
            "topic_tags",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'fact'"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_claim.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "superseded_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_claim.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "source_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
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
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'stale', 'disputed', 'archived')",
            name="ck_knowledge_claim_status",
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'rule', 'decision', 'example', 'glossary', 'other')",
            name="ck_knowledge_claim_kind",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_knowledge_claim_confidence_range",
        ),
    )

    # Promote embedding to pgvector(1536) — round-trips Python lists
    # through the same Vector mapping the rest of the schema uses.
    op.execute(
        "ALTER TABLE knowledge_claim DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )

    op.create_index(
        "ix_knowledge_claim_workspace_id",
        "knowledge_claim",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_claim_status",
        "knowledge_claim",
        ["status"],
    )
    op.create_index(
        "ix_knowledge_claim_supersedes_id",
        "knowledge_claim",
        ["supersedes_id"],
    )
    op.create_index(
        "ix_knowledge_claim_superseded_by_id",
        "knowledge_claim",
        ["superseded_by_id"],
    )
    # Idempotent re-extract: same workspace + same exact claim text
    # (sha) collapses to one row. The reconciliation engine uses
    # embedding-cosine to merge near-duplicates with different wording;
    # this is just the cheap exact-string guard.
    op.create_index(
        "uq_knowledge_claim_workspace_sha",
        "knowledge_claim",
        ["workspace_id", "claim_md_sha"],
        unique=True,
    )
    # GIN on the multi-tag array so topic-views can filter quickly.
    op.execute(
        "CREATE INDEX ix_knowledge_claim_topic_tags "
        "ON knowledge_claim USING gin (topic_tags)"
    )
    # HNSW on embedding — same params as kb_chunks / methodology_chunks
    # so cosine retrieval cost is consistent across the search surfaces.
    op.execute(
        "CREATE INDEX ix_knowledge_claim_embedding "
        "ON knowledge_claim USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # ---- knowledge_source_items: body_md + extraction bookkeeping --------
    op.add_column(
        "knowledge_source_items",
        sa.Column("body_md", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_source_items",
        sa.Column("body_md_sha", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "knowledge_source_items",
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_knowledge_source_items_body_sha",
        "knowledge_source_items",
        ["body_md_sha"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_source_items_body_sha",
        table_name="knowledge_source_items",
    )
    op.drop_column("knowledge_source_items", "extracted_at")
    op.drop_column("knowledge_source_items", "body_md_sha")
    op.drop_column("knowledge_source_items", "body_md")

    op.execute("DROP INDEX IF EXISTS ix_knowledge_claim_embedding")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_claim_topic_tags")
    op.drop_index(
        "uq_knowledge_claim_workspace_sha", table_name="knowledge_claim"
    )
    op.drop_index(
        "ix_knowledge_claim_superseded_by_id", table_name="knowledge_claim"
    )
    op.drop_index(
        "ix_knowledge_claim_supersedes_id", table_name="knowledge_claim"
    )
    op.drop_index("ix_knowledge_claim_status", table_name="knowledge_claim")
    op.drop_index(
        "ix_knowledge_claim_workspace_id", table_name="knowledge_claim"
    )
    op.drop_table("knowledge_claim")
