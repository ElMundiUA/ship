"""bucket_articles: content unit for knowledge buckets (Consolidation Phase 5a)

Revision ID: 0015_bucket_articles
Revises: 0014_bucket_scope_source
Create Date: 2026-04-21

Ships the **article** primitive the Phase 5 plan is built around:
one content row inside a :class:`KnowledgeBucket`. Before this,
``.ship/knowledge/*.md`` files only lived in ``kb_chunks`` (embedded
for the agent's ``search_repo_kb`` tool) and as a stub metadata row
in ``knowledge_buckets``. The markdown body itself had no dedicated
home — the console's /knowledge detail page had to re-fetch via the
GitHub API every time.

This migration adds only the table. Dual-write from
``sync_repo_files`` and the data migration for existing
``bucket_summaries`` rows ship in follow-up commits so each slice
can be reverted independently.

Schema
------

- ``id``             — UUID PK.
- ``bucket_id``      — FK → ``knowledge_buckets.id`` (CASCADE).
- ``slug``           — short id within the bucket; stays stable across
                        version bumps. For one-file repo_files
                        buckets this is ``"main"``; multi-article
                        buckets (Phase 5c, Distiller) use per-section
                        slugs like ``"section-auth-flow"``.
- ``title``          — displayable heading.
- ``body_md``        — markdown content (soft-capped; not embedded
                        here to keep row size bounded).
- ``content_sha``    — hash of ``body_md`` (or the upstream vendor
                        SHA for ``repo_files``). Drives idempotency:
                        unchanged content = no write.
- ``version``        — monotonic int per ``(bucket_id, slug)``;
                        bumped on every content change.
- ``status``         — ``draft`` | ``published`` | ``superseded`` |
                        ``archived``. Published is the default for
                        ingestor-written articles; ``superseded``
                        marks history rows that the distiller /
                        versioning step replaces.
- ``supersedes_id``  — FK → ``bucket_articles.id``. Null on the first
                        version. Populated when a later Phase creates
                        history rows; Phase 5a keeps only the
                        current version and leaves this column null.
- ``provenance``     — JSONB trace of where the article came from:
                        ``{"source_kind": "repo_files",
                          "path": ".ship/knowledge/code-style.md",
                          "branch": "main", "vendor_sha": "..."}``
                        (or ``{"thread_id": "...", "packed_at": "..."}``
                        for agent-memory). Free-form so new source
                        kinds don't need schema bumps.
- ``embedding``      — optional ``vector(1536)``. Populated by the
                        distiller in a later phase; null at Phase 5a.
- ``archived_at``    — non-null when the article's source disappeared
                        (file deleted in the repo, etc.). The
                        bucket's own ``archived_at`` is a separate
                        lifecycle; an article can be archived while
                        its bucket is still live (e.g. a section was
                        removed from a multi-article bucket).
- ``created_at`` / ``updated_at`` — standard timestamps.

Indexes / constraints
---------------------

- UNIQUE ``(bucket_id, slug, version)`` — exact-version lookup + no
  two rows claiming the same version of the same slug.
- Partial UNIQUE ``(bucket_id, slug) WHERE status = 'published'`` —
  one live version per slug at a time. Supersession means the old
  row goes to ``status='superseded'`` before a new ``published`` row
  lands.
- INDEX ``(bucket_id)`` for "list articles in this bucket".
- INDEX ``(status)`` for operational queries ("how many draft
  articles across the platform").

Backfill
--------

None — this revision is add-only. The next commit wires
``sync_repo_files`` to write one article per repo_files bucket, and
a separate commit migrates existing ``bucket_summaries`` rows into
``bucket_articles`` (preserving thread_id in provenance).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0015_bucket_articles"
down_revision: Union[str, None] = "0014_bucket_scope_source"
branch_labels = None
depends_on = None


# Matches :data:`backend.app.db.models.agent_memory.EMBED_DIM`. Changing
# the model is a re-embed pass, not a schema migration — the column
# stays a fixed-dimension vector.
_EMBED_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "bucket_articles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bucket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("content_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'published'"),
        ),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bucket_articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # ``Vector`` is declared the same way it is in the agent-v2
        # migration (0010). When pgvector isn't installed the column
        # falls back to a generic array — fine for unit tests, still
        # correct in production where pgvector is required.
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=True),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'superseded', 'archived')",
            name="ck_bucket_articles_status",
        ),
    )

    op.create_unique_constraint(
        "uq_bucket_articles_bucket_slug_version",
        "bucket_articles",
        ["bucket_id", "slug", "version"],
    )

    # One live "published" article per (bucket, slug). Supersession
    # flips the old row's status BEFORE inserting the new one so
    # this index never sees a transient double.
    op.create_index(
        "uq_bucket_articles_published_slug",
        "bucket_articles",
        ["bucket_id", "slug"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "ix_bucket_articles_bucket_id",
        "bucket_articles",
        ["bucket_id"],
    )
    op.create_index(
        "ix_bucket_articles_status",
        "bucket_articles",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_bucket_articles_status", table_name="bucket_articles")
    op.drop_index("ix_bucket_articles_bucket_id", table_name="bucket_articles")
    op.drop_index(
        "uq_bucket_articles_published_slug", table_name="bucket_articles"
    )
    op.drop_constraint(
        "uq_bucket_articles_bucket_slug_version",
        "bucket_articles",
        type_="unique",
    )
    op.drop_table("bucket_articles")
