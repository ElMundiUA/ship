"""bucket_articles: backfill from bucket_summaries (Consolidation Phase 5b)

Revision ID: 0016_backfill_summary_articles
Revises: 0015_bucket_articles
Create Date: 2026-04-21

Populate ``bucket_articles`` with one row per existing
``bucket_summaries`` row so every ``agent_memory`` bucket has an
article mirror to match the Phase 5a ``repo_files`` work. This is the
last piece of data consolidation before Phase 5c cuts read paths over
to ``bucket_articles``.

Design choices
--------------

- **Pure SQL** so the migration doesn't need the application layer
  (no app imports, no async machinery). pgvector columns copy across
  natively by casting the source column into the target — both sides
  are ``vector(1536)``.
- **Deterministic slug** — ``'thread-' || replace(s.id::text, '-',
  '')``. Mirrors :func:`backend.app.services.bucket_summary_articles.
  article_slug_for_summary` so a later Python-side reconcile run
  produces the exact same slugs and stays idempotent against the SQL
  backfill.
- **SHA via pgcrypto**. ``digest(text, 'sha256')`` requires the
  ``pgcrypto`` extension, so we ``CREATE EXTENSION IF NOT EXISTS`` it
  up front. Ship already uses ``gen_random_uuid()`` which also rides
  on pgcrypto, so this is a no-op on every live database.
- **Provenance** — ``{"source_kind": "agent_memory", "summary_id":
  "...", "thread_id": "..."|null, "created_by_user_id": "..."|null,
  "packed_at": "..."}``. NULL FKs in the source are preserved as
  JSON ``null`` rather than the string ``"None"``.
- **NOT EXISTS guard** so re-running upgrade (or upgrading a database
  where ``pack_topic`` already inserted mirrors for new summaries via
  the Phase 5b dual-write) skips rows that already have an article.
  That's the only interlock we need: the Python mirror uses the same
  deterministic slug, so "already present" is unambiguous.

Downgrade removes only rows that look like summary mirrors — slug
prefix + provenance marker. We can't losslessly undo an agent-memory
write (the summary itself is the canonical store until Phase 5c),
but we can leave it dormant and let the DELETE clean up the mirror.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_backfill_summary_articles"
down_revision: Union[str, None] = "0015_bucket_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto is already required for ``gen_random_uuid()`` but we
    # assert it here so the digest() call below isn't theoretically
    # reachable on a database where the extension is missing.
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    op.execute(
        sa.text(
            """
            INSERT INTO bucket_articles (
                id, bucket_id, slug, title, body_md, content_sha,
                version, status, supersedes_id, provenance, embedding,
                archived_at, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                s.bucket_id,
                'thread-' || replace(s.id::text, '-', ''),
                COALESCE(NULLIF(btrim(s.title), ''), 'Packed topic'),
                s.summary,
                encode(digest(s.summary, 'sha256'), 'hex'),
                1,
                'published',
                NULL,
                jsonb_build_object(
                    'source_kind', 'agent_memory',
                    'summary_id', s.id::text,
                    'thread_id',
                        CASE WHEN s.thread_id IS NULL THEN NULL
                             ELSE s.thread_id::text END,
                    'created_by_user_id',
                        CASE WHEN s.created_by_user_id IS NULL THEN NULL
                             ELSE s.created_by_user_id::text END,
                    'packed_at',
                        to_char(
                            s.created_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                        )
                ),
                s.embedding,
                NULL,
                s.created_at,
                s.created_at
            FROM bucket_summaries s
            WHERE NOT EXISTS (
                SELECT 1
                  FROM bucket_articles a
                 WHERE a.bucket_id = s.bucket_id
                   AND a.slug = 'thread-' || replace(s.id::text, '-', '')
            )
            """
        )
    )


def downgrade() -> None:
    # Only mirrors authored by this migration / the Phase 5b dual-write
    # path match both filters — slug convention + provenance marker.
    # A Distiller-authored agent-memory article (future Phase 6) would
    # have a different slug, so it's safe to leave it alone.
    op.execute(
        sa.text(
            """
            DELETE FROM bucket_articles
             WHERE slug LIKE 'thread-%'
               AND provenance ->> 'source_kind' = 'agent_memory'
            """
        )
    )
