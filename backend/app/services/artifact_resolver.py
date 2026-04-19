"""Multi-source artifact resolver (RFC-0006).

For every workspace, three independent layers can contribute artifacts:

1. ``project``   — overrides pinned at a single project (``projects/<id>/.ship/``)
2. ``workspace`` — entries authored by the workspace itself
3. ``global``    — the public Ship monorepo, mirrored read-only

Each layer can be enabled/disabled per workspace via
``Workspace.catalog_sources``. The resolver merges enabled layers in the
priority order ``project > workspace > global``: when the same
``(kind, id)`` appears in multiple layers, the higher-priority entry wins
and its ``effective_source`` is recorded so the UI / CLI can show provenance
("inherited from global", "overridden in workspace", …).

This v1 implementation reads from local filesystem paths only. Git remotes
are stored in ``ArtifactRepo.url``; a follow-up worker will clone them into
a local cache directory and feed that path into the resolver.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import ArtifactRepo, Workspace
from backend.app.services.artifact_loader import KIND_PLURALS, load_kind_from_root


# Source label literal: lower precedence first.
SOURCE_PRIORITY = ("global", "workspace", "project")


@dataclass(frozen=True, slots=True)
class _Layer:
    """A concrete on-disk source the resolver should scan."""

    source: str  # one of SOURCE_PRIORITY
    root: Path
    repo_id: uuid.UUID | None = None  # None for the global mirror


def _global_root() -> Path:
    """Where the public Ship monorepo lives on this server.

    Defaults to the repo root that ships with ``ship-server`` (mounted
    read-only into the container by docker-compose). Self-hosted operators
    can move this with the ``SHIP_GLOBAL_REPO_PATH`` env var (followup).
    """
    return Path(__file__).resolve().parents[3]


def _resolve_repo_root(repo: ArtifactRepo) -> Path | None:
    """Map an :class:`ArtifactRepo` to a local directory if possible.

    Currently only ``file://`` URLs are dereferenced inline. Git URLs are
    accepted on register but require the (forthcoming) sync worker to
    materialise them under a known local cache path before they can be read.
    """
    parsed = urlparse(repo.url)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path or repo.url).expanduser()
        return path if path.is_dir() else None
    return None


async def _layers_for_workspace(
    session: AsyncSession, workspace: Workspace
) -> list[_Layer]:
    """Materialise the ordered list of sources to read for this workspace."""
    layers: list[_Layer] = []
    sources = workspace.catalog_sources or {}

    if sources.get("global", True):
        layers.append(_Layer(source="global", root=_global_root()))

    if sources.get("workspace", True):
        repo_stmt = (
            select(ArtifactRepo)
            .where(
                ArtifactRepo.workspace_id == workspace.id,
                ArtifactRepo.kind == "workspace",
            )
            .order_by(ArtifactRepo.created_at)
        )
        for repo in (await session.execute(repo_stmt)).scalars().all():
            root = _resolve_repo_root(repo)
            if root is not None:
                layers.append(_Layer(source="workspace", root=root, repo_id=repo.id))

    if sources.get("project", True):
        repo_stmt = (
            select(ArtifactRepo)
            .where(
                ArtifactRepo.workspace_id == workspace.id,
                ArtifactRepo.kind == "project",
            )
            .order_by(ArtifactRepo.created_at)
        )
        for repo in (await session.execute(repo_stmt)).scalars().all():
            root = _resolve_repo_root(repo)
            if root is not None:
                layers.append(_Layer(source="project", root=root, repo_id=repo.id))

    return layers


def _annotate(entry: dict[str, Any], layer: _Layer) -> dict[str, Any]:
    annotated = dict(entry)
    annotated["effective_source"] = layer.source
    if layer.repo_id is not None:
        annotated["source_repo_id"] = str(layer.repo_id)
    return annotated


async def list_kind(
    session: AsyncSession, workspace: Workspace, kind: str
) -> list[dict[str, Any]]:
    """Return the merged list of artifacts for ``kind`` visible to this workspace."""
    if kind not in KIND_PLURALS:
        raise ValueError(f"unknown artifact kind: {kind}")

    layers = await _layers_for_workspace(session, workspace)

    # Merge: iterate from lowest-priority (global) up; later sources overwrite.
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for entry in load_kind_from_root(layer.root, kind):
            merged[entry["id"]] = _annotate(entry, layer)
    return sorted(merged.values(), key=lambda e: e["id"])


async def get_one(
    session: AsyncSession,
    workspace: Workspace,
    kind: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Return the single resolved artifact, or ``None`` if no source has it."""
    if kind not in KIND_PLURALS:
        raise ValueError(f"unknown artifact kind: {kind}")

    layers = await _layers_for_workspace(session, workspace)
    winner: dict[str, Any] | None = None
    for layer in layers:
        for entry in load_kind_from_root(layer.root, kind):
            if entry["id"] == artifact_id:
                winner = _annotate(entry, layer)
                break  # within a single layer, ids are unique
    return winner
