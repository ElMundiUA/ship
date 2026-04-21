"""Agent v2 memory surface (C12 — real agent).

Four new tables + chat_threads extensions layered on top of the C8/C9/C10
agent-surface models:

- :class:`KnowledgeBucket` — named thematic bucket (e.g. ``auth-refactor``)
  used as the agent's "memory" unit. Embedded so the TopicService can
  retrieve a bucket by semantic match on the user's new message.
- :class:`BucketSummary` — one rollup per packed thread under a bucket.
  The natural-language summary is what gets injected as warmed context
  when the user comes back to the same topic; the embedding drives the
  cosine-similarity retrieval.
- :class:`KbChunk` — ``.ship/knowledge/**/*.md`` content, chunked + embedded
  so the ``search_repo_kb`` tool is a single pgvector query.
- :class:`ArtifactFeedback` — feedback filed against a specific artifact
  id (``pattern/cloud-base``, ``workflow/pr-and-ci-gate``, …). Different
  from ``improvements`` (those are proposed changes to the tenant repo);
  this is "hey, this catalog entry is wrong / incomplete".

``pgvector`` columns are declared as :class:`Vector` (from ``pgvector.sqlalchemy``)
which round-trips Python lists to / from ``vector(1536)`` transparently.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


# Matches the migration + :data:`backend.app.services.agent.embedding.EMBED_DIM`.
# ``text-embedding-3-small`` is the OpenAI default; switching models is a
# re-embed pass, not a schema change.
EMBED_DIM = 1536


# -----------------------------------------------------------------------------
# Scope + source enums. Plain string constants (not ``enum.Enum``) because we
# store them as ``String(…)`` in Postgres and want the raw wire value to be
# self-explanatory when you ``psql`` into the DB or ``grep`` the JSON in logs.
# Keep the allowed-value sets in sync with the CHECK constraint in migration
# ``0014_bucket_scope_source``.
# -----------------------------------------------------------------------------


class BucketScope:
    """Visibility layer a bucket belongs to.

    Phase 1 ships with four scopes; ``global`` (cross-workspace, platform
    policies) will be introduced in a later migration once the platform
    admin surfaces exist and ``knowledge_buckets.workspace_id`` is made
    nullable. Until then, any cross-workspace sharing is a resolver
    concern rather than a storage one.

    Inheritance / resolution order (low → high priority — later wins):
    ``workspace → project → repo`` for team buckets, plus ``user`` as a
    parallel private layer that can overlay any of those for the
    signed-in user only.
    """

    WORKSPACE = "workspace"
    PROJECT = "project"
    REPO = "repo"
    USER = "user"

    ALL: tuple[str, ...] = (WORKSPACE, PROJECT, REPO, USER)


class BucketSource:
    """How the bucket obtains its content.

    - ``agent_memory`` — packed chat summaries (the Navigator's existing
      surface). Writes happen via ``pack_topic`` in ``TopicService``.
    - ``repo_files`` — markdown files under ``.ship/knowledge/`` in an
      activated repo. Authoritative source is git; Ship mirrors the
      index so the resolver + RAG can serve them.
    - ``external_static`` — files/URLs uploaded or pasted into Ship
      directly; authoritative source is Ship's object store.
    - ``connector_proxy`` — bucket is a thin index over a live
      third-party source (Confluence space, ServiceNow KB, Notion
      database); content is fetched on read, not stored.
    - ``audio_transcript`` — transcripts ingested from recorded
      interviews (e.g. offboarding); becomes articles via the Distiller.
    """

    AGENT_MEMORY = "agent_memory"
    REPO_FILES = "repo_files"
    EXTERNAL_STATIC = "external_static"
    CONNECTOR_PROXY = "connector_proxy"
    AUDIO_TRANSCRIPT = "audio_transcript"

    ALL: tuple[str, ...] = (
        AGENT_MEMORY,
        REPO_FILES,
        EXTERNAL_STATIC,
        CONNECTOR_PROXY,
        AUDIO_TRANSCRIPT,
    )


class KnowledgeBucket(Base):
    """User-curated bucket holding packed summaries of past topics.

    ``slug`` is unique per ``(workspace, scope_kind, carrier)`` — for
    workspace-scoped buckets that collapses back to the historical
    ``(workspace, slug)`` rule, for repo/project/user scopes the slug
    is unique within that carrier. Slugs stay stable across renames;
    ``name`` is the displayable label.

    ``embedding`` is optional because an empty bucket has nothing to
    embed yet — we only compute it when the user explicitly pins one.
    For agent-memory buckets we primarily use
    :class:`BucketSummary.embedding` for retrieval; other source kinds
    will rely on :class:`KbChunk` (Phase 5 unifies articles across
    sources).

    ``scope_kind`` / ``source_kind`` / ``source_ref`` are the Phase 1
    consolidation surface — see :class:`BucketScope` + :class:`BucketSource`
    for the allowed values and migration ``0014_bucket_scope_source``
    for the CHECK constraint that keeps them aligned with the scope
    carrier FKs (``project_id`` / ``repo_id`` / ``user_id``).
    """

    __tablename__ = "knowledge_buckets"
    __table_args__ = (
        # Partial unique indexes live alongside the table via the
        # migration; SQLAlchemy's ``UniqueConstraint`` can't express
        # ``WHERE`` clauses portably, so we declare them only in the
        # Alembic op and rely on Postgres to enforce them.
        Index("ix_knowledge_buckets_workspace_id", "workspace_id"),
        Index("ix_knowledge_buckets_scope_kind", "workspace_id", "scope_kind"),
        Index(
            "ix_knowledge_buckets_source_kind", "workspace_id", "source_kind"
        ),
        CheckConstraint(
            (
                "(scope_kind = 'workspace' AND project_id IS NULL "
                "AND repo_id IS NULL AND user_id IS NULL) "
                "OR (scope_kind = 'project' AND project_id IS NOT NULL) "
                "OR (scope_kind = 'repo' AND repo_id IS NOT NULL) "
                "OR (scope_kind = 'user' AND user_id IS NOT NULL)"
            ),
            name="ck_knowledge_buckets_scope_carrier",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Consolidation surface (Phase 1) ---------------------------------
    # See :class:`BucketScope` for allowed values. Default keeps existing
    # behaviour: every current row is a workspace-scoped agent-memory
    # bucket.
    scope_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'workspace'")
    )
    source_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'agent_memory'")
    )
    source_ref: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    # Scope carriers: exactly one is non-null (except ``workspace``
    # which has none). Enforced at DB level by the CHECK above.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class BucketSummary(Base):
    """One summary row inside a :class:`KnowledgeBucket`.

    Created on `pack_topic` (explicit "New topic" click or accepted
    topic-shift banner). The agent injects the top-K most similar
    summaries into the next turn's context so a returning conversation
    about the same topic starts warmed.
    """

    __tablename__ = "bucket_summaries"
    __table_args__ = (
        Index("ix_bucket_summaries_bucket_id", "bucket_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_created()


class KbChunk(Base):
    """One chunk of a repo's ``.ship/knowledge`` corpus.

    Idempotent on ``(repo_id, source_path, chunk_index, content_sha)``
    — the indexer uses the SHA to skip re-embedding unchanged content
    on subsequent ingest runs. The HNSW index on ``embedding`` turns
    ``search_repo_kb`` into a single sub-100ms query.
    """

    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint(
            "repo_id",
            "source_path",
            "chunk_index",
            "content_sha",
            name="uq_kb_chunks_repo_path_idx_sha",
        ),
        Index("ix_kb_chunks_workspace_id", "workspace_id"),
        Index("ix_kb_chunks_repo_source", "repo_id", "source_path"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )
    indexed_at: Mapped[datetime] = _ts_created()


class ArtifactFeedback(Base):
    """Feedback filed against a specific catalog artifact.

    Status progression:

    - ``open`` (initial)
    - ``triaged`` (human looked at it, may have edited ``context``)
    - ``merged`` (feedback landed in an upstream artifact PR; URL in
      ``linked_pr_url``)
    - ``closed`` (won't-fix / dup)
    """

    __tablename__ = "artifact_feedback"
    __table_args__ = (
        Index("ix_artifact_feedback_workspace_id", "workspace_id"),
        Index("ix_artifact_feedback_artifact_id", "artifact_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
    )
    linked_pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = [
    "ArtifactFeedback",
    "BucketScope",
    "BucketSource",
    "BucketSummary",
    "EMBED_DIM",
    "KbChunk",
    "KnowledgeBucket",
]
