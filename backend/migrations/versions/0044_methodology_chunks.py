"""methodology_chunks: pgvector-backed table for legacy /search and /fetch endpoints (E13)

Revision ID: 0044_methodology_chunks
Revises: 0043_knowledge_import_sources
Create Date: 2026-04-30

Replaces ChromaDB with a dedicated pgvector table for the unauthenticated
methodology API (/search and /fetch endpoints over documentation/, artifacts/,
and README.md). Carries the same schema as the Chroma index:

- ``id``             — UUID primary key
- ``path``           — e.g. "documentation/concepts.md" or "artifacts/auth-flow/ARTIFACT.md"
- ``chunk_idx``      — integer offset within the path (0-indexed)
- ``body``           — chunk text content
- ``content_sha``    — SHA256 hex of chunk body (for idempotent re-indexing)
- ``embedding``      — vector(1536) pgvector embedding
- ``kind``           — chunk source type: 'doc', 'artifact', or 'readme'
- ``slug``           — optional identifier (e.g. for artifact path decomposition)
- ``indexed_at``     — creation/last-indexed timestamp

Indexes:
- HNSW on embedding (vector_cosine_ops, m=16, ef_construction=64)
- Unique on (path, chunk_idx) to prevent duplicate chunks on re-index
- Btree on kind for filter queries
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0044_methodology_chunks"
down_revision: Union[str, None] = "0043_knowledge_import_sources"
branch_labels = None
depends_on = None


_EMBED_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "methodology_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_sha", sa.Text(), nullable=False),
        sa.Column(
            "embedding",
            postgresql.ARRAY(sa.Float),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=True),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('doc', 'artifact', 'readme')",
            name="ck_methodology_chunks_kind",
        ),
    )

    # Convert embedding column from ARRAY to pgvector type.
    op.execute(
        "ALTER TABLE methodology_chunks DROP COLUMN embedding, "
        f"ADD COLUMN embedding vector({_EMBED_DIM})"
    )

    # HNSW index for fast cosine similarity search (same params as kb_chunks).
    op.execute(
        "CREATE INDEX ix_methodology_chunks_embedding "
        "ON methodology_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # Unique constraint on (path, chunk_idx) for idempotent re-indexing.
    op.create_index(
        "uq_methodology_chunks_path_idx",
        "methodology_chunks",
        ["path", "chunk_idx"],
        unique=True,
    )

    # Btree index on kind for filter queries.
    op.create_index(
        "ix_methodology_chunks_kind",
        "methodology_chunks",
        ["kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_methodology_chunks_kind",
        table_name="methodology_chunks",
    )
    op.drop_index(
        "uq_methodology_chunks_path_idx",
        table_name="methodology_chunks",
    )
    op.execute("DROP INDEX IF EXISTS ix_methodology_chunks_embedding")
    op.drop_table("methodology_chunks")
