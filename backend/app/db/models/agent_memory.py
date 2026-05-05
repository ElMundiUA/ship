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
  id (``pattern/common-base``, ``workflow/pr-and-ci-gate``, …). Different
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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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

    KB-5 (ELS-39, 2026-05-01): ``REPO`` is **deprecated**. Knowledge
    binds to projects, not to specific repos — repo-scoped rows
    polluted the centroid set the routing pipeline (KB-2) reads from.
    The constant stays so legacy reads don't crash, but new writes
    must use ``WORKSPACE`` / ``PROJECT`` / ``USER``. ``ALLOWED_FOR_WRITE``
    is what creation paths should reference; ``ALL`` is read-only
    historical coverage.

    Inheritance / resolution order (low → high priority — later wins):
    ``workspace → project`` for team buckets, plus ``user`` as a
    parallel private layer that can overlay either for the signed-in
    user only.
    """

    WORKSPACE = "workspace"
    PROJECT = "project"
    REPO = "repo"  # deprecated; preserved for legacy read paths only
    USER = "user"

    # Read-side coverage — every value the column has ever held.
    ALL: tuple[str, ...] = (WORKSPACE, PROJECT, REPO, USER)
    # Write-side gate — what new bucket creations are allowed to set.
    ALLOWED_FOR_WRITE: tuple[str, ...] = (WORKSPACE, PROJECT, USER)


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
    - ``promoted`` — a workspace-scope canonical article synthesised
      from a dedup cluster of repo-scope articles (RFC-0008 §I / PR-7B).
      Authoritative source is the promotion flow; the operator edits
      the canonical directly inside the bucket from then on.
    - ``repo_context`` — Ship-owned generated context for an activated
      repository. The repo is the input, but Ship's DB is canonical.
    """

    AGENT_MEMORY = "agent_memory"
    REPO_FILES = "repo_files"
    EXTERNAL_STATIC = "external_static"
    CONNECTOR_PROXY = "connector_proxy"
    AUDIO_TRANSCRIPT = "audio_transcript"
    PROMOTED = "promoted"
    REPO_CONTEXT = "repo_context"

    ALL: tuple[str, ...] = (
        AGENT_MEMORY,
        REPO_FILES,
        EXTERNAL_STATIC,
        CONNECTOR_PROXY,
        AUDIO_TRANSCRIPT,
        PROMOTED,
        REPO_CONTEXT,
    )


class KnowledgeSourceKind:
    """Source adapters that feed a bucket's articles.

    ``KnowledgeBucket.source_kind/source_ref`` remain as compatibility
    projections, but new sync logic should treat ``knowledge_sources`` as
    the durable source configuration and lifecycle surface.
    """

    REPO_CONTEXT = "repo_context"
    CONNECTOR = "connector"
    GIT_DOCS = "git_docs"
    STATIC_UPLOAD = "static_upload"
    AGENT_MEMORY = "agent_memory"
    REPO_FILES = "repo_files"
    AUDIO_TRANSCRIPT = "audio_transcript"
    PROMOTED = "promoted"

    ALL: tuple[str, ...] = (
        REPO_CONTEXT,
        CONNECTOR,
        GIT_DOCS,
        STATIC_UPLOAD,
        AGENT_MEMORY,
        REPO_FILES,
        AUDIO_TRANSCRIPT,
        PROMOTED,
    )


class KnowledgeSourceStatus:
    """Lifecycle state for a bucket source sync."""

    READY = "ready"
    SYNCING = "syncing"
    ERROR = "error"
    DISABLED = "disabled"

    ALL: tuple[str, ...] = (READY, SYNCING, ERROR, DISABLED)


class KnowledgeImportSourceKind:
    """Workspace-level external sources that feed many buckets."""

    NOTION = "notion"
    CONFLUENCE = "confluence"
    STATIC_UPLOAD = "static_upload"
    DOCS_REPO = "docs_repo"
    WEBSITE = "website"

    ALL: tuple[str, ...] = (NOTION, CONFLUENCE, STATIC_UPLOAD, DOCS_REPO, WEBSITE)


class KnowledgeIngestionStatus:
    """Lifecycle for source sync and analysis runs."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"

    ALL: tuple[str, ...] = (PENDING, RUNNING, DONE, ERROR)


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


class KnowledgeImportSource(Base):
    """Workspace-level source whose content is routed into knowledge buckets."""

    __tablename__ = "knowledge_import_sources"
    __table_args__ = (
        Index("ix_knowledge_import_sources_workspace_id", "workspace_id"),
        Index("ix_knowledge_import_sources_kind", "workspace_id", "kind"),
        CheckConstraint(
            "kind IN ('notion', 'confluence', 'static_upload', 'docs_repo', 'website')",
            name="ck_knowledge_import_sources_kind",
        ),
        CheckConstraint(
            "status IN ('ready', 'syncing', 'error', 'disabled')",
            name="ck_knowledge_import_sources_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ready'")
    )
    sync_cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class KnowledgeSourceItem(Base):
    """One external page/file/url discovered inside an import source."""

    __tablename__ = "knowledge_source_items"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_knowledge_source_items_source_external",
        ),
        Index("ix_knowledge_source_items_workspace_id", "workspace_id"),
        Index("ix_knowledge_source_items_source_id", "source_id"),
        Index("ix_knowledge_source_items_fingerprint", "content_fingerprint"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_import_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    item_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Lifted out of ``Improvement.body`` (which is an event queue, not a
    # corpus) so the claim extractor can re-run idempotently keyed on
    # ``body_md_sha``. NULL for legacy rows synced before migration 0059.
    body_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_md_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class KnowledgeIngestionRun(Base):
    """One sync/analyze attempt for a workspace import source."""

    __tablename__ = "knowledge_ingestion_runs"
    __table_args__ = (
        Index("ix_knowledge_ingestion_runs_workspace_id", "workspace_id"),
        Index("ix_knowledge_ingestion_runs_source_id", "source_id"),
        Index("ix_knowledge_ingestion_runs_status", "status"),
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error')",
            name="ck_knowledge_ingestion_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_import_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class KnowledgeSource(Base):
    """Durable source configuration for a knowledge bucket.

    A bucket owns articles; sources explain where those articles came
    from and carry sync state. This lets Ship own generated repo context
    and uploaded content without forcing every knowledge artifact into the
    tenant repository.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        Index("ix_knowledge_sources_workspace_id", "workspace_id"),
        Index("ix_knowledge_sources_bucket_id", "bucket_id"),
        Index("ix_knowledge_sources_kind", "workspace_id", "kind"),
        CheckConstraint(
            (
                "kind IN ('repo_context', 'connector', 'git_docs', "
                "'static_upload', 'agent_memory', 'repo_files', "
                "'audio_transcript', 'promoted')"
            ),
            name="ck_knowledge_sources_kind",
        ),
        CheckConstraint(
            "status IN ('ready', 'syncing', 'error', 'disabled')",
            name="ck_knowledge_sources_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ready'")
    )
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class BucketArticleStatus:
    """Lifecycle of a :class:`BucketArticle`.

    - ``draft`` — written but not yet visible to readers (Distiller
      staging, imports waiting on review).
    - ``published`` — the current, readable version. The partial
      unique index ``uq_bucket_articles_published_slug`` guarantees
      at most one per ``(bucket_id, slug)`` at a time.
    - ``superseded`` — an older version kept around as history. The
      incoming ``published`` row points at this one via
      ``supersedes_id`` so the chain is navigable.
    - ``archived`` — the source disappeared (file deleted in git,
      upload hard-removed). Kept because downstream citations /
      telemetry may reference it; excluded from default reads.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

    ALL: tuple[str, ...] = (DRAFT, PUBLISHED, SUPERSEDED, ARCHIVED)


class BucketArticle(Base):
    """One content unit inside a :class:`KnowledgeBucket`.

    Versioning is per ``(bucket_id, slug)``: every content change
    inserts a new row with ``version = prev.version + 1`` and flips
    the prior row from ``published`` to ``superseded``. The partial
    unique index on ``(bucket_id, slug) WHERE status='published'``
    enforces that exactly one version is live at a time.

    Phase 5a scope: one article per ``repo_files`` bucket (slug =
    ``"main"``, one row total; version bumps on edits but no
    superseded history yet). Multi-article buckets and full history
    ride along on the same table in Phase 5c once the Distiller
    lands.
    """

    __tablename__ = "bucket_articles"
    __table_args__ = (
        # Migration 0015 owns the partial unique index on published
        # rows — SQLAlchemy can't express ``WHERE status='published'``
        # portably, so we declare it Alembic-side and Postgres enforces
        # it. The full (bucket_id, slug, version) uniqueness is here
        # so SQLAlchemy is aware of it for ORM-level deduping.
        UniqueConstraint(
            "bucket_id",
            "slug",
            "version",
            name="uq_bucket_articles_bucket_slug_version",
        ),
        Index("ix_bucket_articles_bucket_id", "bucket_id"),
        Index("ix_bucket_articles_status", "status"),
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded', 'archived')",
            name="ck_bucket_articles_status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'published'")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bucket_articles.id", ondelete="SET NULL"),
        nullable=True,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class BucketArticleSource(Base):
    """Provenance link from a bucket article version to an external item."""

    __tablename__ = "bucket_article_sources"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "source_item_id",
            name="uq_bucket_article_sources_article_item",
        ),
        Index("ix_bucket_article_sources_article_id", "article_id"),
        Index("ix_bucket_article_sources_source_item_id", "source_item_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bucket_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_source_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'primary'")
    )
    created_at: Mapped[datetime] = _ts_created()


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


class DistillerRunStatus:
    """Lifecycle of a :class:`DistillerRun`.

    Phase 6a ships the stub ingest path only — ``queued`` → ``done``
    happens in-process in a single request. Phase 6b plugs the LLM
    classifier in behind a worker queue; ``running`` becomes the
    visible mid-state and ``failed`` the retry-on-error exit.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    ALL: tuple[str, ...] = (QUEUED, RUNNING, DONE, FAILED)


class DistillerRunDecision:
    """Outcome the Distiller reached for a given input blob.

    Mirrors ``{decision: "new" | "update" | "skip"}`` in the public
    contract documented in ``backend/docs/knowledge-consolidation.md``.
    ``error`` is the terminal outcome when the run itself failed
    (distinct from ``skip``, which is a legitimate "nothing to do").
    """

    NEW = "new"
    UPDATE = "update"
    SKIP = "skip"
    ERROR = "error"

    ALL: tuple[str, ...] = (NEW, UPDATE, SKIP, ERROR)


class DistillerRun(Base):
    """One ingest attempt against a :class:`KnowledgeBucket`.

    Created by ``POST /v1/workspaces/{ws}/buckets/{slug}/distill``.
    Each run owns the input summary (``input_ref``) and the resulting
    decision (``new`` / ``update`` / ``skip``). When the decision
    lands a :class:`BucketArticle`, the article ids are persisted in
    ``output_refs['article_ids']`` so a future audit trail or
    regenerate-on-edit flow can walk the provenance.

    Phase 6a persists the full row even for the happy-path stub. The
    decision + output schema is stable enough to consume from the
    console; Phase 6b swaps the classifier without migration work.
    """

    __tablename__ = "distiller_runs"
    __table_args__ = (
        Index("ix_distiller_runs_workspace_id", "workspace_id"),
        Index("ix_distiller_runs_bucket_id", "bucket_id"),
        Index("ix_distiller_runs_status", "status"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_distiller_runs_status",
        ),
        CheckConstraint(
            "decision IS NULL "
            "OR decision IN ('new', 'update', 'skip', 'error')",
            name="ck_distiller_runs_decision",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_buckets.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'queued'")
    )
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Inbound blob metadata — not the content itself (that is the
    # article body). ``{"title_hint": "...", "slug_hint": "...",
    # "bytes": 1234, "source_ref": {...}}`` is the canonical shape.
    input_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Outputs produced by the run. ``{"article_ids": ["..."], "diff":
    # {...}}`` for ``new``/``update``; ``{"reason": "..."}`` for
    # ``skip``/``error``.
    output_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class ClaimStatus:
    """Lifecycle status of a :class:`KnowledgeClaim` row.

    Mirrors the CHECK constraint in migration 0059. Plain string
    constants (not ``enum.Enum``) match the rest of this module's
    convention so ``psql`` and ``grep`` show the wire value verbatim.

    - ``ACTIVE`` — current canon. The claim hasn't been replaced and
      at least one source has confirmed it within the decay window.
    - ``SUPERSEDED`` — replaced by a newer claim via
      ``superseded_by_id``. Kept for the history graph; not returned
      from default search.
    - ``STALE`` — no source has confirmed this claim for N decay-cron
      ticks. Operator can revive (flip back to active) if it was a
      false positive.
    - ``DISPUTED`` — reconciliation engine flagged a conflict that
      needs operator review (hits the inbox).
    - ``ARCHIVED`` — operator-killed, terminal.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    STALE = "stale"
    DISPUTED = "disputed"
    ARCHIVED = "archived"

    ALL = frozenset({ACTIVE, SUPERSEDED, STALE, DISPUTED, ARCHIVED})


class ClaimKind:
    """Coarse type tag on a claim — drives nothing critical, but makes
    operator review and search filters readable.

    Mirrors the CHECK constraint in migration 0059. The extractor
    picks one per claim; mismatches don't poison anything (we'd
    prefer ``other`` over forcing a wrong bucket).
    """

    FACT = "fact"
    RULE = "rule"
    DECISION = "decision"
    EXAMPLE = "example"
    GLOSSARY = "glossary"
    OTHER = "other"

    ALL = frozenset({FACT, RULE, DECISION, EXAMPLE, GLOSSARY, OTHER})


class KnowledgeClaim(Base):
    """One atomic, verifiable assertion extracted from sources.

    The unit of canonical truth in the post-0059 knowledge model.
    Articles (``BucketArticle``) become a derived view rendered on
    top of the active claim set per topic; reconciliation operates
    here, at the fact level, so a stale architecture decision can
    be marked ``superseded`` without rewriting an entire article.

    Multi-tag is on purpose: a claim about Linear FSM is legitimately
    both ``integrations`` and ``architecture-decisions``. The single-
    bucket model forced a false choice and pushed the synth engine
    toward "skip" decisions.

    Idempotency on re-extract is the ``(workspace_id, claim_md_sha)``
    unique index — a second run that produces the same exact text
    no-ops at the DB level. The reconciliation engine handles
    near-duplicates with different wording via embedding cosine.
    """

    __tablename__ = "knowledge_claim"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "claim_md_sha",
            name="uq_knowledge_claim_workspace_sha",
        ),
        Index("ix_knowledge_claim_workspace_id", "workspace_id"),
        Index("ix_knowledge_claim_status", "status"),
        Index("ix_knowledge_claim_supersedes_id", "supersedes_id"),
        Index("ix_knowledge_claim_superseded_by_id", "superseded_by_id"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'stale', 'disputed', 'archived')",
            name="ck_knowledge_claim_status",
        ),
        CheckConstraint(
            "kind IN ('fact', 'rule', 'decision', 'example', 'glossary', 'other')",
            name="ck_knowledge_claim_kind",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_knowledge_claim_confidence_range",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_md: Mapped[str] = mapped_column(Text, nullable=False)
    claim_md_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )
    topic_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("ARRAY[]::text[]"),
    )
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'fact'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_claim.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_claim.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("1.0")
    )
    source_links: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


__all__ = [
    "ArtifactFeedback",
    "BucketArticle",
    "BucketArticleSource",
    "BucketArticleStatus",
    "BucketScope",
    "BucketSource",
    "BucketSummary",
    "ClaimKind",
    "ClaimStatus",
    "DistillerRun",
    "DistillerRunDecision",
    "DistillerRunStatus",
    "EMBED_DIM",
    "KbChunk",
    "KnowledgeBucket",
    "KnowledgeClaim",
    "KnowledgeImportSource",
    "KnowledgeImportSourceKind",
    "KnowledgeIngestionRun",
    "KnowledgeIngestionStatus",
    "KnowledgeSource",
    "KnowledgeSourceItem",
]
