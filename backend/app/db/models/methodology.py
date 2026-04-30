"""ORM model for the ``methodology_chunks`` table (E13 — Chroma → pgvector).

Lives in its own module so the legacy methodology API (``/search``, ``/fetch``)
can keep its single dependency surface (this model + the indexer service in
``backend.app.services.methodology_index``) without coupling to the cloud
platform's bucket models.

Why an ORM model and not raw SQL: pgvector + asyncpg in this codebase always
goes through ``pgvector.sqlalchemy.Vector`` so the dialect handles the binary
``vector(N)`` type properly. The raw ``:q::vector`` cast that the first cut
of ``methodology_index.py`` used silently fell over on insert under asyncpg —
the indexer ran on startup, swallowed the error inside the ``lifespan``
``try/except``, and left ``methodology_chunks`` empty. /search then had
nothing to query and 500'd when the embedder ran without rows to score.
"""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


# Same dim as ``backend.app.services.agent.embedding.EMBED_DIM``. Keep both in
# step on any model swap — the migration locks ``vector(1536)``.
EMBED_DIM = 1536


class MethodologyChunk(Base):
    """One indexable chunk of the methodology corpus.

    Rows are owned by :func:`backend.app.services.methodology_index.reindex_if_stale`,
    which walks ``documentation/``, ``artifacts/**/ARTIFACT.md``, and
    ``README.md`` on backend startup and upserts deltas (``content_sha``
    skip path). Read by the legacy ``/search`` and ``/fetch`` route handlers
    in ``backend/app/main.py``.
    """

    __tablename__ = "methodology_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Repo-relative path. For ARTIFACT.md the indexer rewrites this to the
    # artifact folder so callers can fetch the whole bundle.
    path: Mapped[str] = mapped_column(Text, nullable=False)

    # 0-indexed offset within ``path``.
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)

    # The chunk text (post-frontmatter strip for ARTIFACT.md).
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of ``body`` — drives idempotent reindex (skip when unchanged).
    content_sha: Mapped[str] = mapped_column(Text, nullable=False)

    # 1536-d OpenAI embedding. Nullable so a chunk row can exist while
    # embedding is queued (we don't actually use that path today, but it
    # keeps the model future-proof for a backfill job).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )

    # Coarse content classification — drives optional filtered queries.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    # Artifact id when the source is ``artifacts/<plural>/<id>/ARTIFACT.md``.
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)

    indexed_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('doc', 'artifact', 'readme')",
            name="ck_methodology_chunks_kind",
        ),
        # Match migration 0044 — the unique constraint is named explicitly
        # there. We declare it via ``Index`` (not ``UniqueConstraint``) so
        # the naming convention can't auto-generate a different name and
        # diverge from the migration.
        Index(
            "uq_methodology_chunks_path_idx",
            "path",
            "chunk_idx",
            unique=True,
        ),
        Index("ix_methodology_chunks_kind", "kind"),
    )
