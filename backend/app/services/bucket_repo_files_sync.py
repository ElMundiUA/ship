"""Sync ``.ship/knowledge/*.md`` files into the ``knowledge_buckets`` table.

This service is the Phase 2 companion to
:mod:`backend.app.services.agent.kb_indexer`. Where the indexer chunks
+ embeds knowledge markdown into ``kb_chunks`` for the agent's
``search_repo_kb`` tool, this module surfaces each markdown file as a
distinct **bucket row** so the operator console's ``/knowledge`` page,
the (future) scope resolver, and the CLI all see a first-class,
repo-scoped bucket per file.

Layout decisions
----------------

- **One file ⇢ one bucket row.** The file's basename (minus ``.md``)
  becomes the slug. ``.ship/knowledge/code-style.md`` lands as a
  ``scope_kind='repo'`` + ``source_kind='repo_files'`` bucket with
  slug ``"code-style"``.
- **Git is canonical.** The bucket row is a *mirror* — we never write
  back to the repo from here. Content lives in git; the row carries
  the minimal projection needed to render the list + resolve the
  file on demand (path + content_sha + branch + excerpt).
- **Deletion archives, doesn't drop.** When a file disappears from
  git we set ``archived_at`` on the bucket row. The row stays so
  downstream references (agent memories linking to the bucket,
  telemetry, audit) don't dangle.
- **Idempotency.** Re-running the sync with the same SHA is a no-op.
  The webhook path relies on this: a push that touches 99 other files
  on the repo's default branch must not rewrite 100 rows.
- **Independence from the embedder.** We re-fetch the file list +
  metadata through the same :class:`CodeHostGateway` the indexer uses,
  *without* re-fetching blob contents for unchanged files. When the
  Phase 5 article table lands we'll factor out the shared fetch, but
  for now the GitHub Contents API cost is modest (one tree call +
  one blob per changed file).

What this file deliberately does NOT do
---------------------------------------

- It does not embed (that's ``kb_indexer``).
- It does not talk to the frontend (that's the route in
  :mod:`backend.app.api.v1.routes.knowledge`).
- It does not handle workspace-scoped / project-scoped / user-scoped
  buckets. Those are separate source kinds with their own sync paths.

See ``backend/docs/knowledge-consolidation.md`` Phase 2 for the
surrounding plan.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    CodeHostGateway,
    RepoRef,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.agent.kb_indexer import KB_ROOT


logger = logging.getLogger(__name__)


# Defensive caps. Mirrors :mod:`kb_indexer` so one runaway repo can't
# wedge either surface — the operator either commits a KB dump or
# they don't, and both pipelines treat it the same way.
_MAX_FILES: int = 500
_MAX_FILE_BYTES: int = 256 * 1024

# Excerpt cap. Tuned to look right on the list card without making
# the JSON payload oversized on repos with many buckets.
_EXCERPT_CHARS: int = 280

# Regexes. Cheap enough to compile at import; reused per-file.
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_WS_RE = re.compile(r"\s+")

# Phase 5a: each ``repo_files`` bucket carries exactly one article, with
# a fixed slug. Multi-article buckets (Distiller-authored, audio
# transcripts that split into sections) ship in a later phase and will
# use per-section slugs on the same table.
_ARTICLE_SLUG_MAIN: str = "main"


@dataclass(slots=True)
class SyncReport:
    """Per-run summary so callers can surface progress in the UI.

    Kept narrow on purpose: UI / logs need "did anything move" and
    "how big was the move". The verbose per-file trail goes into
    ``logger.info`` when DEBUG is off, ``logger.debug`` otherwise —
    it's too noisy to keep in a report struct.
    """

    repo_id: str
    files_discovered: int = 0
    buckets_created: int = 0
    buckets_updated: int = 0
    buckets_archived: int = 0
    files_skipped_unchanged: int = 0
    files_skipped_binary: int = 0
    files_skipped_too_big: int = 0
    # Phase 5a: dual-write into ``bucket_articles``. Counted separately
    # from bucket ops because bucket + article lifecycles can drift
    # once multi-article buckets land (e.g. a single KB edit may bump
    # an article without touching the bucket row).
    articles_created: int = 0
    articles_updated: int = 0
    articles_unchanged: int = 0
    articles_archived: int = 0
    errors: list[str] = field(default_factory=list)


async def sync_repo_files(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> SyncReport:
    """**Deprecated (KB-5 / ELS-39).** No-op: returns an empty report.

    Repo-scoped ``repo_files`` buckets are gone. Production callers
    were removed in the same change; this stub stays for one release
    so any out-of-tree caller (or test harness) gets a clean empty
    response instead of an ImportError. The body below is kept for
    historical reference until the next cleanup PR deletes the file.
    """
    return SyncReport()


async def _sync_repo_files_legacy(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> SyncReport:
    """Pre-KB-5 implementation, retained for reference. Not exported."""
    s = settings or get_settings()
    gw = gateway or GitHubCodeHost(
        install.installation_id, settings=s, client=http_client
    )
    owner, name = _owner_repo(repo)
    ref = RepoRef(kind="github", owner=owner, repo=name)

    report = SyncReport(repo_id=str(repo.id))

    # ---- Discover markdown files under the KB root ----
    all_paths = await gw.list_files(ref, ref_sha=repo.default_branch or None)
    kb_paths = [
        p for p in all_paths
        if p.startswith(KB_ROOT + "/") and p.lower().endswith(".md")
    ]
    if len(kb_paths) > _MAX_FILES:
        logger.warning(
            "repo %s has %d KB files, bucket sync capped at %d",
            repo.full_name, len(kb_paths), _MAX_FILES,
        )
        kb_paths = kb_paths[:_MAX_FILES]
    report.files_discovered = len(kb_paths)

    # ---- Load existing repo-scoped buckets once ----
    existing_rows = (
        await session.execute(
            select(KnowledgeBucket).where(
                and_(
                    KnowledgeBucket.workspace_id == repo.workspace_id,
                    KnowledgeBucket.repo_id == repo.id,
                    KnowledgeBucket.scope_kind == BucketScope.REPO,
                    KnowledgeBucket.source_kind == BucketSource.REPO_FILES,
                )
            )
        )
    ).scalars().all()
    existing_by_path: dict[str, KnowledgeBucket] = {}
    for row in existing_rows:
        # ``source_ref`` is the authoritative pointer back into git.
        # Rows without a ``path`` are a pre-Phase-2 artifact (unlikely
        # given migration 0014 defaults, but we skip defensively).
        path = _source_ref_path(row)
        if path is None:
            continue
        existing_by_path[path] = row

    seen_paths: set[str] = set()

    # ---- Upsert one bucket per discovered file ----
    for path in kb_paths:
        try:
            blob = await gw.get_blob(
                ref, path=path, ref_sha=repo.default_branch or None
            )
        except FileNotFoundError:
            # Listed but gone by the time we asked for it — treat as
            # if it was never there. The cleanup loop below will
            # archive any stale row.
            continue
        except Exception as exc:  # pragma: no cover — gateway/IO fault
            report.errors.append(f"{path}: fetch failed: {exc}")
            continue

        seen_paths.add(path)

        if blob.encoding != "utf-8":
            report.files_skipped_binary += 1
            continue
        if blob.size > _MAX_FILE_BYTES:
            report.files_skipped_too_big += 1
            continue

        existing = existing_by_path.get(path)
        if existing is not None:
            if _source_ref_sha(existing) == blob.sha and existing.archived_at is None:
                # Fast-path: SHA matches + not archived → no writes on
                # the bucket. The article still gets a quick sanity
                # check: if ``bucket_articles`` is missing a row for
                # this bucket (e.g. we're on a backfill run from before
                # Phase 5a) we repair it here so the table is eventually
                # consistent without a separate backfill job.
                report.files_skipped_unchanged += 1
                action = await _upsert_article_for_bucket(
                    session, bucket=existing, blob=blob, repo=repo
                )
                _tally_article_action(report, action)
                continue
            _apply_blob_to_row(existing, blob=blob, repo=repo)
            if existing.archived_at is not None:
                existing.archived_at = None
            report.buckets_updated += 1
            bucket_for_article = existing
        else:
            row = _build_row(repo=repo, blob=blob)
            # Pre-assign id client-side so the article row can carry
            # the FK inside the same flush without an extra round-trip.
            # ``server_default=gen_random_uuid()`` still wins if this
            # is left as None, so behaviour is unchanged for callers
            # that don't go through this path.
            row.id = uuid.uuid4()
            session.add(row)
            report.buckets_created += 1
            bucket_for_article = row

        action = await _upsert_article_for_bucket(
            session, bucket=bucket_for_article, blob=blob, repo=repo
        )
        _tally_article_action(report, action)

    # ---- Archive rows whose file vanished ----
    now = datetime.now(timezone.utc)
    for path, row in existing_by_path.items():
        if path in seen_paths:
            continue
        if row.archived_at is not None:
            # Already archived on a previous run — don't bump the
            # timestamp or it'll look like the file keeps leaving.
            continue
        row.archived_at = now
        report.buckets_archived += 1
        if await _archive_article_for_bucket(session, bucket=row, now=now):
            report.articles_archived += 1

    await session.flush()
    return report


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _owner_repo(repo: WorkspaceRepo) -> tuple[str, str]:
    """Split ``owner/name`` from :attr:`WorkspaceRepo.full_name`."""
    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        raise ValueError(
            f"WorkspaceRepo.full_name {repo.full_name!r} is not owner/repo."
        )
    return owner, name


def _build_row(*, repo: WorkspaceRepo, blob: BlobContent) -> KnowledgeBucket:
    row = KnowledgeBucket(
        workspace_id=repo.workspace_id,
        repo_id=repo.id,
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        slug=_slug_from_path(blob.path),
        name="",  # filled by _apply_blob_to_row
        description=None,
    )
    _apply_blob_to_row(row, blob=blob, repo=repo)
    return row


def _apply_blob_to_row(
    row: KnowledgeBucket, *, blob: BlobContent, repo: WorkspaceRepo
) -> None:
    """Populate name/description/source_ref from blob content.

    Shared so create + update paths agree on the projection rules and
    neither drifts. ``name`` comes from the first markdown H1 (falling
    back to a humanised slug); ``description`` is a short excerpt for
    list cards. ``source_ref`` is the tuple the resolver needs to
    re-fetch the file on demand — ``{path, content_sha, branch}``.
    """
    row.name = _title_from_markdown(blob.content) or _humanise(row.slug)
    row.description = _excerpt(blob.content)
    row.source_ref = _build_source_ref(repo=repo, blob=blob)


def _build_source_ref(
    *, repo: WorkspaceRepo, blob: BlobContent
) -> dict[str, Any]:
    return {
        "path": blob.path,
        "content_sha": blob.sha,
        "branch": repo.default_branch or "main",
        "size": blob.size,
    }


def _source_ref_path(row: KnowledgeBucket) -> str | None:
    ref = row.source_ref or {}
    value = ref.get("path") if isinstance(ref, dict) else None
    return value if isinstance(value, str) else None


def _source_ref_sha(row: KnowledgeBucket) -> str | None:
    ref = row.source_ref or {}
    value = ref.get("content_sha") if isinstance(ref, dict) else None
    return value if isinstance(value, str) else None


def _slug_from_path(path: str) -> str:
    """``.ship/knowledge/foo-bar.md`` → ``foo-bar``.

    Slugs are bound to filenames by convention — operators can rename
    a file and the old bucket will archive while the new one appears.
    """
    stem = path.rsplit("/", 1)[-1]
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem


def _humanise(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title() or slug


def _title_from_markdown(text: str) -> str | None:
    match = _H1_RE.search(text)
    if match is None:
        return None
    title = match.group(1).strip()
    return title or None


async def _upsert_article_for_bucket(
    session: AsyncSession,
    *,
    bucket: KnowledgeBucket,
    blob: BlobContent,
    repo: WorkspaceRepo,
) -> str:
    """Upsert the canonical ``main`` article for ``bucket`` from ``blob``.

    Returns one of ``"created" | "updated" | "unchanged"`` so the
    caller can tally the action into :class:`SyncReport`. The
    ``content_sha`` stored on the article is a hash of the markdown
    body (not the vendor blob SHA) — two files with identical content
    pulled from different remotes don't churn versions.

    Versioning semantics (Phase 5a, one-article-per-bucket):

    - No prior article → insert version 1.
    - Published article with same ``content_sha`` → no-op.
    - Published article with different ``content_sha`` → flip it to
      ``superseded`` and insert the next version with
      ``supersedes_id`` pointing back at it.
    - No *published* article but archived rows exist → resurrection.
      Insert version = ``max(existing_versions) + 1`` so the UNIQUE
      ``(bucket_id, slug, version)`` constraint never collides with
      dormant history.
    """
    body = blob.content
    content_sha = _sha256_hex(body)

    current = (
        await session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.slug == _ARTICLE_SLUG_MAIN,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()

    if current is not None and current.content_sha == content_sha:
        return "unchanged"

    if current is not None:
        # Flip the old row BEFORE inserting the new one. Postgres
        # evaluates the partial-unique index per-statement, so as long
        # as the UPDATE precedes the INSERT inside the flush (which it
        # will — SQLAlchemy orders UPDATEs before INSERTs by default)
        # there is no transient double-published window.
        current.status = BucketArticleStatus.SUPERSEDED
        new_version = current.version + 1
        supersedes_id: uuid.UUID | None = current.id
    else:
        supersedes_id = None
        max_prev = (
            await session.execute(
                select(func.max(BucketArticle.version)).where(
                    BucketArticle.bucket_id == bucket.id,
                    BucketArticle.slug == _ARTICLE_SLUG_MAIN,
                )
            )
        ).scalar()
        new_version = (int(max_prev) if max_prev is not None else 0) + 1

    title = (bucket.name or "").strip() or _humanise(bucket.slug)
    provenance = {
        "source_kind": BucketSource.REPO_FILES,
        "path": blob.path,
        "vendor_sha": blob.sha,
        "branch": repo.default_branch or "main",
    }
    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=_ARTICLE_SLUG_MAIN,
        title=title[:512],
        body_md=body,
        content_sha=content_sha,
        version=new_version,
        status=BucketArticleStatus.PUBLISHED,
        supersedes_id=supersedes_id,
        provenance=provenance,
    )
    session.add(article)
    return "created" if current is None else "updated"


async def _archive_article_for_bucket(
    session: AsyncSession, *, bucket: KnowledgeBucket, now: datetime
) -> bool:
    """Flip the current published article to ``archived``.

    Returns ``True`` if something changed, ``False`` if there was no
    live article to archive (bucket was pre-Phase-5a or never synced).
    Superseded rows stay as-is; they're part of history and not subject
    to this lifecycle.
    """
    current = (
        await session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.slug == _ARTICLE_SLUG_MAIN,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()
    if current is None:
        return False
    current.status = BucketArticleStatus.ARCHIVED
    current.archived_at = now
    return True


def _tally_article_action(report: SyncReport, action: str) -> None:
    if action == "created":
        report.articles_created += 1
    elif action == "updated":
        report.articles_updated += 1
    elif action == "unchanged":
        report.articles_unchanged += 1


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(text: str) -> str | None:
    """First ~280 chars after the opening H1 (if any), whitespace-normalised."""
    if not text:
        return None
    # Drop the first H1 line so the excerpt isn't just the title again.
    lines = text.splitlines()
    body_lines: list[str] = []
    skipped = False
    for line in lines:
        if not skipped and _H1_RE.match(line):
            skipped = True
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if not body:
        return None
    normalised = _WS_RE.sub(" ", body).strip()
    if len(normalised) > _EXCERPT_CHARS:
        return normalised[:_EXCERPT_CHARS].rstrip() + "…"
    return normalised


__all__ = ["SyncReport", "sync_repo_files"]
