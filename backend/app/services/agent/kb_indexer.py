"""KB indexer: ingest ``.ship/knowledge/**/*.md`` into ``kb_chunks``.

The agent's ``search_repo_kb`` tool is a vector search over the
``kb_chunks`` table; this module is what *populates* it. One activated
:class:`~backend.app.db.models.integrations.WorkspaceRepo` at a time.

Design notes
------------

- **Scope** is narrow by design: only files under ``.ship/knowledge/``
  ending in ``.md`` get ingested. That's the knob operators use to
  curate what the agent sees. We deliberately do *not* index the whole
  repo in the first cut — full-repo indexing costs money (embeddings +
  storage) and encourages the agent to quote random code back at the
  user. Operators who want broader grounding use the ``get_repo_file``
  tool or add files to ``.ship/knowledge/`` explicitly.

- **Chunking** is paragraph-oriented with a hard char cap so a single
  blob can't blow up embed costs. We split on blank lines, then
  re-bin to target ~800 chars / chunk. Headings attach to the chunk
  that immediately follows them so retrieval carries the section
  context.

- **Diffing** is path+sha based. If the Git blob SHA for a KB doc
  hasn't moved since the last successful index, we skip the
  re-embedding entirely. Re-runs on an unchanged repo are therefore
  cheap; this is what makes wiring the indexer to push webhooks
  (Day-3 polish) safe.

- **Idempotency** is per-repo: we load the current set of
  ``kb_chunks`` for the repo, diff against the incoming set, and
  ``DELETE``-then-``INSERT`` the delta. This lets a knowledge doc
  being renamed / deleted actually disappear from search — the
  alternative (insert-only) would leave ghosts behind.

- **Concurrency**: one indexer run per repo at a time. Callers
  (webhook handler + manual reindex endpoint) serialize through a
  per-repo advisory lock. Within a run, embeddings are sent to
  OpenAI in batches of 64 (the SDK default limit is much higher, but
  64 keeps a single slow batch from blocking the whole run for
  minutes).
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import KbChunk, KbIndexingRun
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    CodeHostGateway,
    RepoRef,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.agent.embedding import EMBED_DIM, embed_texts

logger = logging.getLogger(__name__)


# Only files under this path get ingested. The string lives in one
# place so operators can grep for it in the docs and the code agrees.
KB_ROOT: Final[str] = ".ship/knowledge"

# Target chunk size. Picked so that ~10 chunks fit in a typical
# retrieval window without blowing up the prompt. Strictly a soft
# target — we never split mid-paragraph to hit it exactly.
_CHUNK_TARGET_CHARS: Final[int] = 800
# Hard cap — paragraphs larger than this get split on sentence
# boundaries. Beyond this, embeddings stop being semantically useful
# because too many concepts share one vector.
_CHUNK_HARD_CAP_CHARS: Final[int] = 1600
# How many texts we embed per OpenAI call. 64 is a compromise: big
# enough to amortise network latency, small enough that one slow
# batch doesn't stall the whole indexing run.
_EMBED_BATCH: Final[int] = 64

# Defensive caps so a misconfigured repo can't wedge a run:
#   * cap total files so a runaway ``.ship/knowledge/`` (someone
#     dumping 10k auto-generated markdown files) doesn't blow our
#     budget on one tenant,
#   * cap per-file size so a committed SQL dump doesn't either.
_MAX_FILES: Final[int] = 500
_MAX_FILE_BYTES: Final[int] = 256 * 1024


@dataclass(slots=True)
class IndexReport:
    """Per-run summary so callers can surface progress in the UI.

    The numbers are intentionally concrete: "processed 12 files,
    skipped 4 unchanged, wrote 83 chunks" is the shape the manual
    reindex endpoint streams back so the operator sees the indexer
    actually doing work.
    """

    repo_id: str
    files_discovered: int = 0
    files_indexed: int = 0
    files_skipped_unchanged: int = 0
    files_skipped_too_big: int = 0
    files_skipped_binary: int = 0
    chunks_deleted: int = 0
    chunks_written: int = 0


async def reindex_repo_kb(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> IndexReport:
    """Rebuild ``kb_chunks`` for one activated repo.

    Drops chunks for files that no longer exist, updates chunks whose
    source SHA changed, and skips files whose SHA matches what's
    already in the DB. Returns an :class:`IndexReport` so the caller
    can render per-file counts.
    """
    s = settings or get_settings()
    gw = gateway or GitHubCodeHost(
        install.installation_id, settings=s, client=http_client
    )
    owner, name = _owner_repo(repo)
    ref = RepoRef(kind="github", owner=owner, repo=name)

    report = IndexReport(repo_id=str(repo.id))

    # Step 1 — list every path under ``.ship/knowledge/``. We rely on
    # ``list_files`` returning the whole tree; the gateway already
    # truncates at 5k paths, which is two orders of magnitude above
    # the KB files we realistically expect.
    all_paths = await gw.list_files(ref, ref_sha=repo.default_branch or None)
    kb_paths = [
        p for p in all_paths
        if p.startswith(KB_ROOT + "/") and p.lower().endswith(".md")
    ]
    report.files_discovered = len(kb_paths)
    if len(kb_paths) > _MAX_FILES:
        logger.warning(
            "repo %s has %d KB files, truncating to %d",
            repo.full_name, len(kb_paths), _MAX_FILES,
        )
        kb_paths = kb_paths[:_MAX_FILES]

    # Step 2 — fetch existing chunks once so the diff is in-memory.
    existing_rows = (
        await session.execute(
            select(KbChunk).where(KbChunk.repo_id == repo.id)
        )
    ).scalars().all()
    existing_by_path: dict[str, list[KbChunk]] = {}
    for row in existing_rows:
        existing_by_path.setdefault(row.source_path, []).append(row)

    seen_paths: set[str] = set()
    blobs_to_embed: list[tuple[str, BlobContent, list[str]]] = []

    # Step 3 — fetch blobs and diff on SHA. Sequential: the upstream
    # rate limit is the bottleneck, not us, and concurrent fetches
    # against the contents API earn us 403s fast.
    for path in kb_paths:
        try:
            blob = await gw.get_blob(ref, path=path, ref_sha=repo.default_branch or None)
        except FileNotFoundError:
            # Between listing and fetching, someone deleted the file.
            # Skip; the cleanup loop below will drop any orphaned
            # chunks.
            continue
        seen_paths.add(path)
        if blob.encoding != "utf-8":
            report.files_skipped_binary += 1
            continue
        if blob.size > _MAX_FILE_BYTES:
            report.files_skipped_too_big += 1
            continue

        current_chunks = existing_by_path.get(path, [])
        if current_chunks and all(c.content_sha == blob.sha for c in current_chunks):
            # Unchanged — the entire file was last indexed at this
            # exact SHA, so every chunk is still valid.
            report.files_skipped_unchanged += 1
            continue

        chunks = _chunk_markdown(blob.content)
        if not chunks:
            # File is empty or pure whitespace — delete any stale
            # chunks and move on.
            if current_chunks:
                await _delete_chunks_for_path(session, repo.id, path)
                report.chunks_deleted += len(current_chunks)
            continue
        blobs_to_embed.append((path, blob, chunks))

    # Step 4 — embed in batches. We flatten first so one OpenAI call
    # covers chunks from multiple files; the (path, index) tuple
    # tells us where to write the vector back.
    flat_texts: list[str] = []
    flat_origins: list[tuple[str, BlobContent, int]] = []
    for path, blob, chunks in blobs_to_embed:
        for idx, chunk_text in enumerate(chunks):
            flat_texts.append(chunk_text)
            flat_origins.append((path, blob, idx))

    vectors: list[list[float]] = []
    for start in range(0, len(flat_texts), _EMBED_BATCH):
        batch = flat_texts[start:start + _EMBED_BATCH]
        vectors.extend(await embed_texts(batch, settings=s))

    # Sanity: the embeddings service promises one vector per input.
    # Fail loud if that ever drifts — a silent misalignment would
    # teach the agent to cite the wrong file forever.
    if len(vectors) != len(flat_texts):
        raise RuntimeError(
            f"embed_texts returned {len(vectors)} vectors for "
            f"{len(flat_texts)} inputs (repo={repo.full_name})"
        )
    for v in vectors:
        if len(v) != EMBED_DIM:
            raise RuntimeError(
                f"embed_texts returned {len(v)}-d vector, expected {EMBED_DIM}"
            )

    # Step 5 — replace chunks for the paths we re-embedded. We do a
    # path-scoped delete first so a file whose chunk count shrank
    # doesn't leave stragglers behind.
    rewritten_paths = {path for path, _, _ in blobs_to_embed}
    for path in rewritten_paths:
        dropped = await _delete_chunks_for_path(session, repo.id, path)
        report.chunks_deleted += dropped

    rows_to_add: list[KbChunk] = []
    for (path, blob, idx), vector, chunk_text in zip(
        flat_origins, vectors, flat_texts
    ):
        rows_to_add.append(
            KbChunk(
                workspace_id=repo.workspace_id,
                repo_id=repo.id,
                source_path=path,
                chunk_index=idx,
                content=chunk_text,
                content_sha=blob.sha,
                embedding=vector,
            )
        )
    session.add_all(rows_to_add)
    report.chunks_written = len(rows_to_add)
    report.files_indexed = len(rewritten_paths)

    # Step 6 — garbage-collect chunks for files that disappeared
    # since the last run.
    orphan_paths = set(existing_by_path) - seen_paths
    for path in orphan_paths:
        dropped = await _delete_chunks_for_path(session, repo.id, path)
        report.chunks_deleted += dropped

    await session.flush()
    return report


def _report_to_stats(report: IndexReport) -> dict[str, int | str]:
    """Project :class:`IndexReport` into the run row's ``stats`` JSONB.

    Keeps the wire shape identical to what ``probe_repo_kb_indexing``
    promises so a callers reading either path see the same keys.
    """
    return {
        "files_discovered": report.files_discovered,
        "files_indexed": report.files_indexed,
        "files_skipped_unchanged": report.files_skipped_unchanged,
        "files_skipped_too_big": report.files_skipped_too_big,
        "files_skipped_binary": report.files_skipped_binary,
        "chunks_deleted": report.chunks_deleted,
        "chunks_written": report.chunks_written,
    }


def _advisory_lock_key_for_repo(repo_id: _uuid.UUID | str) -> int:
    """Stable signed-int64 key for ``pg_advisory_xact_lock(bigint)``.

    Postgres advisory-lock keys are 64-bit signed; we hash the UUID
    bytes into the bottom 8 bytes so concurrent push + agent triggers
    on the same repo always pick the same key (and different repos
    almost never collide). The namespace prefix (``kb_indexer`` here)
    keeps us from colliding with future advisory-locked features.
    """
    raw = str(repo_id).encode("utf-8")
    digest = hashlib.blake2b(b"kb_indexer:" + raw, digest_size=8).digest()
    # Convert to signed 64-bit so it fits Postgres' ``bigint`` advisory
    # lock argument exactly (asyncpg would otherwise refuse a value
    # outside [-2^63, 2^63-1]).
    value = int.from_bytes(digest, "big", signed=False)
    if value >= 1 << 63:
        value -= 1 << 64
    return value


async def create_kb_indexing_run(
    session: AsyncSession,
    *,
    workspace_id: _uuid.UUID,
    repo_id: _uuid.UUID,
    trigger: str,
    created_by_user_id: _uuid.UUID | None = None,
) -> KbIndexingRun:
    """Insert a fresh ``pending`` :class:`KbIndexingRun` row.

    Split out so the trigger HTTP/agent surface can return the
    ``run_id`` immediately (AC #1: ~200 ms) while the heavy lifting
    runs in a FastAPI background task. Caller commits.
    """
    if trigger not in {"agent", "push", "manual"}:
        raise ValueError(f"unknown kb_indexing trigger: {trigger!r}")
    run = KbIndexingRun(
        id=_uuid.uuid4(),
        workspace_id=workspace_id,
        repo_id=repo_id,
        status="pending",
        trigger=trigger,
        stats={},
        created_by_user_id=created_by_user_id,
    )
    session.add(run)
    await session.flush()
    return run


async def execute_kb_indexing_run(
    session: AsyncSession,
    *,
    run_id: _uuid.UUID,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
) -> KbIndexingRun:
    """Drive one ``KbIndexingRun`` row through its pending → done lifecycle.

    Contract:

    1. Resolve the run, its repo, and the GitHub install. Bail to
       ``status='error'`` with a structured message if any of those
       are gone (suspended install, repo deleted, etc.) — the row is
       still useful as the audit trail.
    2. ``SELECT pg_advisory_xact_lock(:k)`` on a key derived from
       ``repo_id`` so concurrent push + agent triggers on the same
       repo serialise (AC #7). The lock auto-releases on
       commit/rollback because it's transaction-scoped.
    3. Flip ``pending → running``, run :func:`reindex_repo_kb`, persist
       the :class:`IndexReport` into ``stats``, transition to ``done``.
       Any exception during the indexer run gets captured into
       ``status='error'`` + ``error`` so we never bubble out of the
       background task as an uncaught.

    Caller owns commit. For background tasks see
    :func:`run_kb_indexing_background` which mints its own session.
    """
    s = settings or get_settings()
    run = await session.get(KbIndexingRun, run_id)
    if run is None:
        # Lost row — caller is racing with a DELETE. Nothing to do.
        return run  # type: ignore[return-value]
    if run.status not in ("pending", "running"):
        # Idempotent re-entry: another worker already drove this run
        # through its lifecycle. Don't re-run.
        return run

    now = datetime.now(timezone.utc)
    repo = await session.get(WorkspaceRepo, run.repo_id)
    if repo is None:
        run.status = "error"
        run.error = "repo_not_found"
        run.finished_at = now
        await session.flush()
        return run
    if repo.installation_id is None:
        run.status = "error"
        run.error = "github_install_missing"
        run.finished_at = now
        await session.flush()
        return run
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        run.status = "error"
        run.error = "github_install_missing"
        run.finished_at = now
        await session.flush()
        return run

    # Serialise with any concurrent push / agent reindex on this repo.
    # The lock is transaction-scoped: when the outer session commits or
    # rolls back, Postgres releases it automatically — no manual unlock
    # path to leak.
    lock_key = _advisory_lock_key_for_repo(repo.id)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)").bindparams(k=lock_key)
    )

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    await session.flush()

    try:
        report = await reindex_repo_kb(
            session, repo, install, settings=s, gateway=gateway
        )
    except Exception as exc:  # noqa: BLE001 — capture every error onto the row
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"[:4000]
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        logger.warning(
            "kb_indexing run %s for repo %s failed: %s",
            run.id, repo.full_name, exc,
        )
        return run

    run.stats = _report_to_stats(report)
    run.status = "done"
    run.finished_at = datetime.now(timezone.utc)
    run.error = None
    await session.flush()
    logger.info(
        "kb_indexing run %s for %s: files_indexed=%d chunks_written=%d",
        run.id,
        repo.full_name,
        report.files_indexed,
        report.chunks_written,
    )
    return run


async def run_kb_indexing_background(run_id: _uuid.UUID) -> None:
    """Background-task entry point for the agent / HTTP trigger paths.

    Mints its own :class:`AsyncSession` so the request connection
    doesn't stay open through the GitHub + OpenAI fan-out (the
    sync→async migration risk called out in the ticket).
    Commits on success; on an uncaught error it opens a second
    short transaction to stamp the row ``error='background task
    crashed'`` so the per-repo advisory lock can't permanently jam
    the next run.
    """
    from backend.app.db.session import get_sessionmaker

    s = get_settings()
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            try:
                await execute_kb_indexing_run(
                    session, run_id=run_id, settings=s
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception:
        # The advisory lock was released when the failed transaction
        # rolled back. Stamp the row in a fresh transaction so probe
        # surfaces something useful instead of a permanent ``running``.
        logger.exception(
            "kb_indexing background task crashed for run %s", run_id
        )
        try:
            async with sessionmaker() as recovery:
                run = await recovery.get(KbIndexingRun, run_id)
                if run is not None and run.status in ("pending", "running"):
                    run.status = "error"
                    run.error = "background task crashed"
                    run.finished_at = datetime.now(timezone.utc)
                    await recovery.commit()
        except Exception:  # noqa: BLE001 — best effort
            logger.exception(
                "failed to stamp run %s as crashed after background failure",
                run_id,
            )


async def _delete_chunks_for_path(
    session: AsyncSession, repo_id, path: str
) -> int:
    """Delete every :class:`KbChunk` for ``(repo_id, path)``; return count."""
    result = await session.execute(
        delete(KbChunk).where(
            KbChunk.repo_id == repo_id, KbChunk.source_path == path
        )
    )
    return int(result.rowcount or 0)


def _owner_repo(repo: WorkspaceRepo) -> tuple[str, str]:
    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        raise ValueError(
            f"WorkspaceRepo.full_name {repo.full_name!r} is not owner/repo."
        )
    return owner, name


_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+")


def _chunk_markdown(text: str) -> list[str]:
    """Paragraph-based chunker with sentence fallback.

    The goals, in priority order:

    1. Never split mid-sentence.
    2. Attach a heading to the chunk it introduces.
    3. Target ~``_CHUNK_TARGET_CHARS``, cap at
       ``_CHUNK_HARD_CAP_CHARS``.

    Returns an empty list if ``text`` is empty or whitespace-only so
    the caller can drop the file cleanly.
    """
    if not text or not text.strip():
        return []
    paragraphs = [p.strip() for p in _PARA_SPLIT_RE.split(text) if p.strip()]
    if not paragraphs:
        return []

    # First pass: fold headings forward so they don't become
    # standalone chunks.
    folded: list[str] = []
    pending_heading: str | None = None
    for para in paragraphs:
        first_line = para.splitlines()[0] if para else ""
        if _HEADING_RE.match(first_line) and len(para.splitlines()) == 1:
            pending_heading = para
            continue
        combined = f"{pending_heading}\n\n{para}" if pending_heading else para
        folded.append(combined)
        pending_heading = None
    # Trailing heading with no body — keep it so we don't lose the
    # section title entirely.
    if pending_heading is not None:
        folded.append(pending_heading)

    # Second pass: bin paragraphs into target-sized chunks.
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in folded:
        if len(para) > _CHUNK_HARD_CAP_CHARS:
            # Flush the current buffer before we split the big para —
            # otherwise the split fragments end up mixed with
            # unrelated content.
            if buf:
                chunks.append("\n\n".join(buf))
                buf = []
                buf_len = 0
            chunks.extend(_split_long_paragraph(para))
            continue
        if buf and buf_len + len(para) + 2 > _CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(buf))
            buf = [para]
            buf_len = len(para)
        else:
            buf.append(para)
            buf_len += len(para) + (2 if buf else 0)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _split_long_paragraph(paragraph: str) -> list[str]:
    """Split a paragraph that overflows the hard cap on sentence boundaries."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    if not sentences:
        return [paragraph[:_CHUNK_HARD_CAP_CHARS]]
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for sentence in sentences:
        if buf and buf_len + len(sentence) + 1 > _CHUNK_TARGET_CHARS:
            out.append(" ".join(buf))
            buf = [sentence]
            buf_len = len(sentence)
        else:
            buf.append(sentence)
            buf_len += len(sentence) + (1 if buf else 0)
    if buf:
        out.append(" ".join(buf))
    return out


__all__ = [
    "IndexReport",
    "KB_ROOT",
    "create_kb_indexing_run",
    "execute_kb_indexing_run",
    "reindex_repo_kb",
    "run_kb_indexing_background",
]
