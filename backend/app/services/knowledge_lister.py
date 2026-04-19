"""Read knowledge buckets from a workspace's registered repos.

Each registered :class:`backend.app.db.models.tenancy.ArtifactRepo` is scanned
for ``.ship/knowledge/*.md`` files. Every markdown file becomes a "bucket"
entry, summarising:

- ``slug``     — filename without extension (canonical id, used in the URL)
- ``title``    — first H1 in the file, falling back to the slug humanised
- ``visibility`` — ``project`` | ``workspace`` (mirrors the repo kind)
- ``size``, ``updated_at`` — filesystem metadata
- ``excerpt``  — first ~280 chars after the H1, for the index card

This is the v1 cut: one markdown file per bucket, no embeddings yet. The
tenant repos commit `.ship/knowledge/<slug>.md` files themselves
(through their own agent / CI lane); this lister surfaces them read-only.

When the embedding indexer lands (RFC-0006 follow-up) we'll keep the same
"bucket = file in .ship/knowledge/" mapping and add ``chunks``/``embeddings``
fields without breaking the wire shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import ArtifactRepo, Workspace


@dataclass(frozen=True, slots=True)
class KnowledgeBucket:
    slug: str
    title: str
    visibility: str  # "project" | "workspace"
    repo_id: str
    repo_url: str
    path: str
    size: int
    updated_at: str  # ISO 8601 UTC
    excerpt: str
    body: str | None = None  # only populated by ``get_one``


_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_KNOWLEDGE_DIR = Path(".ship") / "knowledge"
_MAX_BODY_BYTES = 256 * 1024  # don't slurp pathological 100MB markdown


def _resolve_repo_root(repo: ArtifactRepo) -> Path | None:
    """Mirror :func:`backend.app.services.artifact_resolver._resolve_repo_root`.

    Only ``file://`` paths are dereferenced; remote URLs await the GitHub
    App integration that will replace the legacy git-sync cache.
    """
    parsed = urlparse(repo.url)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path or repo.url).expanduser()
        return path if path.is_dir() else None
    return None


def _humanise(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def _strip_h1(body: str) -> str:
    """Drop the first H1 heading so the excerpt isn't just the title again."""
    lines = body.splitlines()
    out: list[str] = []
    skipped = False
    for line in lines:
        if not skipped and re.match(r"^\s*#\s+", line):
            skipped = True
            continue
        out.append(line)
    return "\n".join(out).strip()


def _entry_for(
    repo: ArtifactRepo, path: Path, *, with_body: bool
) -> KnowledgeBucket | None:
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    h1_match = _H1_RE.search(text)
    title = h1_match.group(1).strip() if h1_match else _humanise(path.stem)

    after_title = _strip_h1(text)
    excerpt = re.sub(r"\s+", " ", after_title)[:280].strip()
    if len(after_title) > 280:
        excerpt += "…"

    return KnowledgeBucket(
        slug=path.stem,
        title=title,
        visibility=repo.kind,
        repo_id=str(repo.id),
        repo_url=repo.url,
        path=str(path),
        size=stat.st_size,
        updated_at=_iso(stat.st_mtime),
        excerpt=excerpt,
        body=text[:_MAX_BODY_BYTES] if with_body else None,
    )


def _iso(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


async def _enabled_repos(
    session: AsyncSession, workspace: Workspace
) -> list[ArtifactRepo]:
    """Repos whose layer is enabled in ``workspace.catalog_sources``.

    Knowledge piggybacks on the same catalog toggles: turning off the
    ``project`` layer in workspace settings hides project-scoped buckets
    from both the catalog *and* the knowledge index.
    """
    sources = workspace.catalog_sources or {}
    kinds: list[str] = []
    if sources.get("workspace", True):
        kinds.append("workspace")
    if sources.get("project", True):
        kinds.append("project")
    if not kinds:
        return []
    stmt = (
        select(ArtifactRepo)
        .where(
            ArtifactRepo.workspace_id == workspace.id,
            ArtifactRepo.kind.in_(kinds),
        )
        .order_by(ArtifactRepo.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_buckets(
    session: AsyncSession, workspace: Workspace
) -> list[KnowledgeBucket]:
    """Return every bucket file across all enabled repos.

    Slugs collide across repos? Project beats workspace, mirroring the
    catalog resolver precedence — so a project-level ``brandbook.md``
    shadows the workspace-level one with the same slug.
    """
    repos = await _enabled_repos(session, workspace)
    rank = {"workspace": 1, "project": 2}
    winners: dict[str, KnowledgeBucket] = {}
    for repo in repos:
        root = _resolve_repo_root(repo)
        if root is None:
            continue
        knowledge_dir = root / _KNOWLEDGE_DIR
        if not knowledge_dir.is_dir():
            continue
        for child in sorted(knowledge_dir.iterdir(), key=lambda p: p.name):
            if not child.is_file() or child.suffix.lower() != ".md":
                continue
            entry = _entry_for(repo, child, with_body=False)
            if entry is None:
                continue
            current = winners.get(entry.slug)
            if current is None or rank[entry.visibility] > rank[current.visibility]:
                winners[entry.slug] = entry
    return sorted(winners.values(), key=lambda e: e.slug)


async def get_bucket(
    session: AsyncSession, workspace: Workspace, slug: str
) -> KnowledgeBucket | None:
    """Return the winning entry for ``slug``, with its full body."""
    repos = await _enabled_repos(session, workspace)
    rank = {"workspace": 1, "project": 2}
    winner: KnowledgeBucket | None = None
    for repo in repos:
        root = _resolve_repo_root(repo)
        if root is None:
            continue
        candidate = root / _KNOWLEDGE_DIR / f"{slug}.md"
        if not candidate.is_file():
            continue
        entry = _entry_for(repo, candidate, with_body=True)
        if entry is None:
            continue
        if winner is None or rank[entry.visibility] > rank[winner.visibility]:
            winner = entry
    return winner


def bucket_to_dict(b: KnowledgeBucket) -> dict[str, object]:
    """Wire-shape projection — keeps the response schema versioned cleanly."""
    out: dict[str, object] = {
        "slug": b.slug,
        "title": b.title,
        "visibility": b.visibility,
        "repo_id": b.repo_id,
        "repo_url": b.repo_url,
        "path": b.path,
        "size": b.size,
        "updated_at": b.updated_at,
        "excerpt": b.excerpt,
    }
    if b.body is not None:
        out["body"] = b.body
    return out


__all__ = [
    "KnowledgeBucket",
    "bucket_to_dict",
    "get_bucket",
    "list_buckets",
]
