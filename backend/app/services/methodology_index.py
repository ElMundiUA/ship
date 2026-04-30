"""Pgvector-backed methodology index (replaces ChromaDB; RFC E13).

The legacy unauthenticated methodology API (``/search``, ``/fetch``) used
to talk to ChromaDB. After RFC E13, all vector search runs on Postgres
+ pgvector via the ``methodology_chunks`` table (migration 0044).

This module owns:

- the **walk** (``documentation/*``, ``artifacts/**/ARTIFACT.md``,
  ``README.md`` — the same surface Chroma indexed);
- the **chunker** (1200 chars, 180-char overlap — kept identical to
  Chroma so the released CLI sees no result drift);
- the **embedder** (delegates to
  :func:`backend.app.services.agent.embedding.embed_texts`, so the
  methodology index and the cloud-platform buckets share one OpenAI
  config and one EMBED_DIM);
- the **upsert** (idempotent: chunks whose ``content_sha`` already
  matches in the DB are skipped — so a cold restart does not re-embed
  the entire corpus, only the deltas);
- the **search** (cosine distance via
  ``MethodologyChunk.embedding.cosine_distance``, returning the same
  JSON shape as the old Chroma path so the released CLI sees no
  contract drift).

The module is independent of FastAPI; ``backend/app/main.py`` calls
:func:`reindex_if_stale` during startup and :func:`search` from the
``/search`` route handler.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.methodology import EMBED_DIM, MethodologyChunk
from backend.app.db.session import get_engine
from backend.app.services.agent.embedding import embed_texts


log = logging.getLogger(__name__)


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATHS = ("documentation", "README.md")
ARTIFACTS_ROOT = APP_ROOT / "artifacts"


# ---------------------------------------------------------------------------
# Walk + chunk
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Chunk:
    """One indexable unit produced by walking the repo."""

    path: str  # repo-relative; for ARTIFACT.md, the artifact folder path
    chunk_idx: int
    body: str
    content_sha: str
    kind: str  # "doc" | "artifact" | "readme"
    slug: str | None  # artifact slug if applicable


def _allowed_files() -> list[Path]:
    """Mirror IndexStore._allowed_files from the old Chroma path."""
    files: list[Path] = []
    for entry in DEFAULT_PATHS:
        candidate = APP_ROOT / entry
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(sorted(candidate.rglob("*.md")))
    if ARTIFACTS_ROOT.is_dir():
        files.extend(sorted(ARTIFACTS_ROOT.rglob("ARTIFACT.md")))
    return [p for p in files if p.is_file()]


def _index_path_for(path: Path) -> str:
    """Return the path the CLI should use to fetch this content again.

    For ``ARTIFACT.md`` files inside ``artifacts/<plural>/<id>/``, return
    the artifact folder path so clients can fetch the whole bundle.
    Other files use their plain repo-relative path.
    """
    rel = path.relative_to(APP_ROOT)
    if path.name == "ARTIFACT.md" and len(rel.parts) >= 3 and rel.parts[0] == "artifacts":
        return str(rel.parent)
    return str(rel)


def _kind_for(path: Path) -> str:
    if path.name == "ARTIFACT.md":
        return "artifact"
    if path.name == "README.md":
        return "readme"
    return "doc"


def _slug_for(path: Path) -> str | None:
    rel = path.relative_to(APP_ROOT)
    if path.name == "ARTIFACT.md" and len(rel.parts) >= 3 and rel.parts[0] == "artifacts":
        return rel.parts[2]  # the artifact id directory
    return None


def _index_text_for(path: Path) -> str:
    """Strip frontmatter from ARTIFACT.md so embeddings see the body only."""
    text_body = path.read_text(encoding="utf-8", errors="ignore")
    if path.name == "ARTIFACT.md" and text_body.startswith("---\n"):
        end = text_body.find("\n---\n", 4)
        if end != -1:
            return text_body[end + len("\n---\n"):]
    return text_body


def _chunk_text(body: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    """Same chunker as the legacy Chroma path — keep CLI results comparable."""
    clean = re.sub(r"\s+\n", "\n", body).strip()
    if len(clean) <= chunk_size:
        return [clean] if clean else []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        chunks.append(clean[start:end])
        if end == len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def _walk_chunks(files: Iterable[Path]) -> Iterable[_Chunk]:
    for file_path in files:
        indexed_path = _index_path_for(file_path)
        kind = _kind_for(file_path)
        slug = _slug_for(file_path)
        text_body = _index_text_for(file_path)
        for idx, chunk_body in enumerate(_chunk_text(text_body)):
            sha = hashlib.sha256(chunk_body.encode("utf-8")).hexdigest()
            yield _Chunk(
                path=indexed_path,
                chunk_idx=idx,
                body=chunk_body,
                content_sha=sha,
                kind=kind,
                slug=slug,
            )


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IndexResult:
    """Stats from a :func:`reindex_if_stale` run, useful for logs / health."""

    files_walked: int
    chunks_seen: int
    chunks_new_or_changed: int
    chunks_unchanged: int
    chunks_pruned: int


async def reindex_if_stale(*, force: bool = False) -> IndexResult:
    """Walk the methodology corpus and upsert into ``methodology_chunks``.

    Idempotent: chunks whose ``(path, chunk_idx, content_sha)`` already
    exist in the DB are skipped (no re-embed). Chunks present in DB but
    no longer produced by the walk are deleted, so the index follows the
    repo as files are added/removed.

    ``force`` re-embeds every chunk regardless of SHA — use only when
    the embedding model itself has changed.
    """
    files = _allowed_files()
    seen_chunks = list(_walk_chunks(files))
    seen_keys = {(c.path, c.chunk_idx) for c in seen_chunks}

    engine = get_engine()
    async with AsyncSession(engine) as session:
        existing = await _existing_index(session)
        stale: list[_Chunk] = []
        for chunk in seen_chunks:
            key = (chunk.path, chunk.chunk_idx)
            if force or existing.get(key) != chunk.content_sha:
                stale.append(chunk)
        unchanged = len(seen_chunks) - len(stale)

        # Embed and upsert in batches so a thousand-chunk corpus stays
        # within OpenAI's per-request soft limit.
        new_or_changed = 0
        for batch in _batched(stale, size=64):
            embeddings = await embed_texts([c.body for c in batch])
            await _upsert_chunks(session, batch, embeddings)
            new_or_changed += len(batch)

        # Prune chunks that are no longer produced by the walk.
        pruned = await _prune_missing(session, seen_keys)
        await session.commit()

    return IndexResult(
        files_walked=len(files),
        chunks_seen=len(seen_chunks),
        chunks_new_or_changed=new_or_changed,
        chunks_unchanged=unchanged,
        chunks_pruned=pruned,
    )


async def _existing_index(
    session: AsyncSession,
) -> dict[tuple[str, int], str]:
    rows = await session.execute(
        select(
            MethodologyChunk.path,
            MethodologyChunk.chunk_idx,
            MethodologyChunk.content_sha,
        )
    )
    return {(r[0], r[1]): r[2] for r in rows.all()}


async def _upsert_chunks(
    session: AsyncSession,
    chunks: list[_Chunk],
    embeddings: list[list[float]],
) -> None:
    """ON CONFLICT-aware bulk insert via the Postgres dialect.

    Going through the dialect-level ``insert(...).on_conflict_do_update``
    keeps the path/chunk_idx unique-index conflict resolution server-side
    and lets the ``Vector`` type adapter serialize the embedding properly
    on asyncpg. The earlier raw-SQL ``::vector`` cast fell over silently
    here.
    """
    if not chunks:
        return
    if len(chunks) != len(embeddings):  # pragma: no cover — defensive
        raise RuntimeError(
            f"embedder returned {len(embeddings)} vectors for {len(chunks)} chunks"
        )
    rows = []
    for c, e in zip(chunks, embeddings, strict=True):
        if len(e) != EMBED_DIM:  # pragma: no cover — defensive
            raise RuntimeError(
                f"embedder returned {len(e)}-d vector; expected {EMBED_DIM}"
            )
        rows.append(
            {
                "path": c.path,
                "chunk_idx": c.chunk_idx,
                "body": c.body,
                "content_sha": c.content_sha,
                "embedding": e,
                "kind": c.kind,
                "slug": c.slug,
            }
        )
    stmt = pg_insert(MethodologyChunk).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["path", "chunk_idx"],
        set_={
            "body": stmt.excluded.body,
            "content_sha": stmt.excluded.content_sha,
            "embedding": stmt.excluded.embedding,
            "kind": stmt.excluded.kind,
            "slug": stmt.excluded.slug,
        },
    )
    await session.execute(stmt)


async def _prune_missing(
    session: AsyncSession, seen_keys: set[tuple[str, int]]
) -> int:
    rows = await session.execute(
        select(MethodologyChunk.path, MethodologyChunk.chunk_idx)
    )
    to_delete = [(r[0], r[1]) for r in rows.all() if (r[0], r[1]) not in seen_keys]
    if not to_delete:
        return 0
    for path, chunk_idx in to_delete:
        await session.execute(
            delete(MethodologyChunk).where(
                MethodologyChunk.path == path,
                MethodologyChunk.chunk_idx == chunk_idx,
            )
        )
    return len(to_delete)


def _batched(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search(query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Cosine-similarity search; same JSON shape as the legacy Chroma path."""
    if not query.strip():
        return []
    [embedding] = await embed_texts([query])
    engine = get_engine()
    async with AsyncSession(engine) as session:
        dist_col = MethodologyChunk.embedding.cosine_distance(embedding).label("dist")
        rows = await session.execute(
            select(
                MethodologyChunk.id,
                MethodologyChunk.path,
                MethodologyChunk.chunk_idx,
                MethodologyChunk.body,
                MethodologyChunk.kind,
                MethodologyChunk.slug,
                dist_col,
            )
            .where(MethodologyChunk.embedding.isnot(None))
            .order_by(dist_col)
            .limit(top_k)
        )
        out: list[dict[str, Any]] = []
        for row in rows.all():
            body = row[3] or ""
            snippet = body[:260].replace("\n", " ").strip()
            out.append(
                {
                    "id": f"{row[1]}::chunk-{row[2]}",
                    "path": row[1],
                    "chunk_index": row[2],
                    "distance": float(row[6]) if row[6] is not None else None,
                    "snippet": snippet,
                    "kind": row[4],
                    "slug": row[5],
                }
            )
        return out
