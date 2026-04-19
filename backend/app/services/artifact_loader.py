"""Read artifacts from a single root directory.

Pure-filesystem; no DB, no HTTP. The cloud platform's resolver
(:mod:`backend.app.services.artifact_resolver`) calls this once per source
and merges the results.

Reuses :func:`backend.app.main._load_kind`'s frontmatter parser
(``_split_frontmatter`` / ``_build_entry``) so the wire shape stays
identical to the unauthenticated ``/patterns`` etc. endpoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Kind → plural-folder mapping, kept in sync with ``backend.app.main.ARTIFACT_KINDS``.
KIND_PLURALS: dict[str, str] = {
    "pattern": "patterns",
    "tool": "tools",
    "workflow": "workflows",
    "collection": "collections",
}


def load_kind_from_root(root: Path, kind: str) -> list[dict[str, Any]]:
    """Return the list of artifact entries for ``kind`` under ``<root>/artifacts/<plural>/``.

    Returns an empty list when the directory is missing — a workspace that has
    not yet contributed any artifacts is not an error.

    The returned entries are the same shape as ``_build_entry`` produces for
    the legacy ``/patterns``-style endpoints, so callers can mix them.
    """
    if kind not in KIND_PLURALS:
        raise ValueError(f"unknown artifact kind: {kind}")

    # Imported here to avoid a circular import at module load time.
    from backend.app.main import _build_entry, _split_frontmatter

    plural = KIND_PLURALS[kind]
    base = root / "artifacts" / plural
    if not base.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        artifact_path = child / "ARTIFACT.md"
        if not artifact_path.is_file():
            continue
        try:
            full = artifact_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _split_frontmatter(full, artifact_path)
        # Skip mismatched folders silently here (the global monorepo gates on
        # this in the unauthenticated route, but per-workspace repos are
        # user-authored and we don't want a single broken file to take the
        # whole list down).
        if meta.get("id") != child.name:
            continue
        entries.append(_build_entry(meta, body, full, plural, artifact_path))
    return entries
