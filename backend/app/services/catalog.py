"""Artifact catalog service — typed access to ``artifacts/**/ARTIFACT.md``.

The catalog is the source of truth for patterns, tools and
collections (the latter group includes presets like ``preset-web-app``).
Until Day-4 Phase-2 the operator console and pipeline runtime only
talked to a small hardcoded slice of it — we knew about
``pr-and-ci-gate`` because its filename was baked into
:mod:`backend.app.api.v1.routes.pipelines`. Everything else (the other
four default pipelines, the presets that drive them, the tool docs)
existed in the filesystem but was invisible to the backend.

This module closes that gap with a single typed accessor that:

* Walks ``artifacts/<plural>/<slug>/ARTIFACT.md`` once per signature
  (``plural + mtime + count``) and caches the parsed frontmatter — the
  same shape :mod:`backend.app.main` already uses for the public
  ``/patterns`` / ``/tools`` / ``/collections`` endpoints.
* Exposes convenience look-ups for the dispatcher and the install flow:
  ``workflow_install_filename`` / ``read_starter_yaml`` /
  ``list_presets``. The workflow helpers are thin shims over
  :mod:`backend.app.services.starter_workflows` — RFC-0007 Phase 6
  retired the public ``artifact_kind=workflow`` layer but the
  Pipeline install flow still needs to commit four baked-in starter
  YAMLs into ``.github/workflows/``; those now live in the backend
  ``resources/`` tree, not the public catalog.
* Deliberately swallows no errors silently: a malformed ARTIFACT.md
  raises :class:`CatalogError` so callers can decide whether to degrade
  (dashboard) or 500 (install endpoint).

``main.py`` still owns the HTTP surface; we just re-use its loader so
both code paths see the same cache and we don't parse the filesystem
twice per request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

__all__ = [
    "CatalogError",
    "CatalogArtifact",
    "ARTIFACTS_ROOT",
    "get_collection",
    "preset_bundle_files",
    "workflow_install_filename",
    "read_starter_yaml",
    "list_presets",
    "list_collections",
    "list_patterns",
    "list_tools",
    "KNOWLEDGE_STARTERS",
    "knowledge_starter_files",
]


class CatalogError(RuntimeError):
    """Raised when the on-disk catalog can't be parsed.

    Separated from ``HTTPException`` so non-HTTP callers (pipeline
    dispatcher, preset seeding) don't end up accidentally raising a
    framework exception outside a request context.
    """


import logging as _logging
import os as _os


_logger = _logging.getLogger(__name__)


def _discover_artifacts_root() -> Path:
    """Resolve the ``artifacts/`` directory at *import* time.

    Three lookup strategies, in order:

    1. ``SHIP_ARTIFACTS_ROOT`` env var — absolute override for air-gapped
       or non-standard deploys.
    2. Walk upward from this file's directory until we hit a parent
       that contains an ``artifacts/`` folder. Works for in-tree dev
       runs and for the Docker image where the repo root is ``/app``.
    3. Fall back to ``<cwd>/artifacts`` (useful when the image layout
       changes but CWD still holds the catalog, e.g. a stripped-down
       container rebuild).

    Crucially, this helper **never raises**. If none of the strategies
    resolve to a real directory we log a warning and hand back the
    would-be path anyway; every downstream helper degrades to "empty
    catalog" instead of nuking the uvicorn boot path. The Phase-3
    incident (Bunny crashloop because ``COPY artifacts`` was missing
    from the backend Dockerfile) is the exact failure mode this
    guard is protecting against.
    """
    override = _os.environ.get("SHIP_ARTIFACTS_ROOT")
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate.is_dir():
            return candidate
        _logger.warning(
            "SHIP_ARTIFACTS_ROOT=%s does not point at an existing directory; "
            "falling back to auto-discovery.",
            override,
        )

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "artifacts"
        if candidate.is_dir():
            return candidate

    fallback = (Path.cwd() / "artifacts").resolve()
    if fallback.is_dir():
        return fallback

    _logger.warning(
        "Could not locate the artifacts/ directory (checked "
        "SHIP_ARTIFACTS_ROOT, parents of %s, and CWD=%s). Catalog "
        "endpoints will return empty lists until the directory is "
        "mounted or the image is rebuilt with COPY artifacts.",
        here,
        Path.cwd(),
    )
    return fallback


ARTIFACTS_ROOT: Final[Path] = _discover_artifacts_root()


_PLURAL_BY_KIND: Final[dict[str, str]] = {
    "pattern": "patterns",
    "tool": "tools",
    "collection": "collections",
}

_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "version",
    "content_sha256",
    "channel",
)


# ---------------------------------------------------------------------------
# Parsing helpers — mirror ``main.py`` but raise :class:`CatalogError` so we
# don't reach for FastAPI's HTTPException from non-HTTP code paths.
# ---------------------------------------------------------------------------


_YAML_RESERVED_PREFIXES = ("@", "`", "%")


def _normalize_inline_lists(raw: str) -> str:
    """Quote inline-list items that start with reserved YAML chars.

    ``authors: [@elmundi/ship-core]`` parses otherwise — PyYAML rejects
    leading ``@`` as a reserved directive indicator.
    """
    import re

    def repl(match: re.Match[str]) -> str:
        head = match.group(1)
        body = match.group(2)
        items: list[str] = []
        for raw_item in body.split(","):
            item = raw_item.strip()
            if not item:
                items.append("")
                continue
            if item[0] in {'"', "'"}:
                items.append(item)
                continue
            if item[0] in _YAML_RESERVED_PREFIXES:
                items.append('"' + item.replace('"', '\\"') + '"')
                continue
            items.append(item)
        return f"{head}[{', '.join(items)}]"

    return re.sub(
        r"^(\s*[\w.-]+\s*:\s*)\[([^\[\]\n]*)\]\s*$", repl, raw, flags=re.MULTILINE
    )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise CatalogError(f"{path} is missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise CatalogError(f"{path} has unterminated frontmatter")
    raw = text[4:end]
    body = text[end + len("\n---\n") :]
    try:
        meta = yaml.safe_load(_normalize_inline_lists(raw)) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path} has invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise CatalogError(f"{path} frontmatter must be a mapping")
    return meta, body


class CatalogArtifact:
    """Typed view over one parsed ARTIFACT.md record.

    Exposes just the fields the backend actually consumes — full body
    and raw metadata stay accessible via :attr:`body` / :attr:`raw` for
    code that wants to stream the content or re-serialise it.
    """

    __slots__ = ("kind", "id", "name", "version", "channel", "group", "tags",
                 "spec", "content_sha256", "updated_at", "deprecated",
                 "replaced_by", "yanked", "description", "body", "raw",
                 "source_path")

    def __init__(
        self,
        *,
        kind: str,
        meta: dict[str, Any],
        body: str,
        source_path: Path,
    ) -> None:
        self.kind = kind
        self.id = str(meta.get("id") or source_path.parent.name)
        self.name = meta.get("name")
        self.version = meta.get("version")
        self.channel = meta.get("channel")
        self.group = meta.get("group")
        self.tags = list(meta.get("tags") or [])
        spec = meta.get("spec")
        self.spec = spec if isinstance(spec, dict) else {}
        self.content_sha256 = meta.get("content_sha256")
        self.updated_at = meta.get("updated_at")
        self.deprecated = bool(meta.get("deprecated", False))
        self.replaced_by = meta.get("replaced_by")
        self.yanked = bool(meta.get("yanked", False))
        self.description = str(meta.get("description") or "").strip()
        self.body = body
        self.raw = meta
        self.source_path = source_path

    @property
    def preset_id(self) -> str | None:
        preset = self.spec.get("preset_id")
        return preset if isinstance(preset, str) and preset else None

    def to_summary(self) -> dict[str, Any]:
        """Serialisation shape mirrored at ``/v1/catalog/*`` endpoints."""
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "channel": self.channel,
            "group": self.group,
            "tags": list(self.tags),
            "description": self.description,
            "content_sha256": self.content_sha256,
            "updated_at": self.updated_at,
            "deprecated": self.deprecated,
            "replaced_by": self.replaced_by,
            "yanked": self.yanked,
            "spec": dict(self.spec),
            "preset_id": self.preset_id,
        }


# ---------------------------------------------------------------------------
# Loader with mtime-based cache (identical signature to ``main.py`` so both
# consumers stay consistent).
# ---------------------------------------------------------------------------


_cache: dict[str, tuple[tuple[str, float, int], list[CatalogArtifact]]] = {}


def _dir_signature(plural: str) -> tuple[str, float, int]:
    base = ARTIFACTS_ROOT / plural
    if not base.is_dir():
        return (plural, 0.0, 0)
    listing: list[str] = []
    mtime = base.stat().st_mtime
    for child in base.iterdir():
        if not child.is_dir():
            continue
        listing.append(child.name)
        artifact_file = child / "ARTIFACT.md"
        if artifact_file.is_file():
            mtime = max(mtime, artifact_file.stat().st_mtime)
    return (plural, mtime, len(listing))


def _load_kind(kind: str) -> list[CatalogArtifact]:
    plural = _PLURAL_BY_KIND.get(kind)
    if plural is None:
        raise CatalogError(f"unknown artifact kind: {kind!r}")
    signature = _dir_signature(plural)
    cached = _cache.get(kind)
    if cached is not None and cached[0] == signature:
        return cached[1]

    base = ARTIFACTS_ROOT / plural
    entries: list[CatalogArtifact] = []
    if base.is_dir():
        for child in sorted(base.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            artifact_file = child / "ARTIFACT.md"
            if not artifact_file.is_file():
                continue
            text = artifact_file.read_text(encoding="utf-8")
            meta, body = _split_frontmatter(text, artifact_file)
            missing = [f for f in _REQUIRED_FIELDS if f not in meta]
            if missing:
                raise CatalogError(
                    f"{artifact_file} missing required fields: {', '.join(missing)}"
                )
            if meta.get("id") and meta["id"] != child.name:
                raise CatalogError(
                    f"{artifact_file} id {meta['id']!r} != folder name {child.name!r}"
                )
            entries.append(
                CatalogArtifact(
                    kind=kind, meta=meta, body=body, source_path=artifact_file
                )
            )

    _cache[kind] = (signature, entries)
    return entries


def invalidate_cache() -> None:
    """Drop the in-memory cache (used by tests that mutate ``artifacts/``)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_patterns() -> list[CatalogArtifact]:
    return _load_kind("pattern")


def list_tools() -> list[CatalogArtifact]:
    return _load_kind("tool")


def list_collections() -> list[CatalogArtifact]:
    return _load_kind("collection")


def list_presets() -> list[CatalogArtifact]:
    """Collections whose ``group`` is ``preset`` (e.g. ``preset-web-app``)."""
    return [c for c in list_collections() if c.group == "preset"]


def get_collection(collection_id: str) -> CatalogArtifact | None:
    for entry in list_collections():
        if entry.id == collection_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# Starter workflow shims (RFC-0007 Phase 6)
#
# Kept here so existing Pipeline-install callers don't have to import
# yet another module. The helpers delegate to
# :mod:`backend.app.services.starter_workflows`; the artifact catalog
# itself no longer knows anything about workflow YAMLs.
# ---------------------------------------------------------------------------


def workflow_install_filename(workflow_id: str) -> str | None:
    from backend.app.services import starter_workflows

    return starter_workflows.install_filename(workflow_id)


def read_starter_yaml(workflow_id: str) -> str | None:
    from backend.app.services import starter_workflows

    return starter_workflows.read_yaml(workflow_id)


def preset_bundle_files(
    preset_id: str,
    *,
    repo_full_name: str | None = None,
) -> list[tuple[str, str]]:
    """Return ``(path, content)`` tuples to commit for a preset's install bundle.

    Contains one workflow YAML per pipeline kind that the preset
    enables (skipping kinds whose starter is YAML-less, i.e.
    ``code_map``) plus a ``.ship/config.yml`` stub identifying the
    preset so downstream ``shipctl`` / agent tooling knows which
    shape to assume.

    The caller (``install_bundle`` route) is responsible for passing
    the list into ``commit_bundle_pr``.
    """
    # Lazy imports keep the module import graph flat and also mean we
    # don't pay the cost of loading default_pipelines on every catalog
    # lookup.
    from backend.app.services import starter_workflows
    from backend.app.services.default_pipelines import (
        DEFAULT_PIPELINES,
        PRESET_ENABLED_KINDS,
    )

    enabled_kinds = PRESET_ENABLED_KINDS.get(preset_id, frozenset())
    files: list[tuple[str, str]] = []
    included_kinds: list[str] = []
    for spec in DEFAULT_PIPELINES:
        if spec.kind not in enabled_kinds:
            continue
        entry = starter_workflows.get(spec.workflow_id)
        if entry is None:
            continue
        content = entry.read_yaml()
        if not content:
            continue
        files.append((entry.install_target, content))
        included_kinds.append(spec.kind)

    # .ship/config.yml — minimal, declarative. Lets the CLI and future
    # agents know which preset the repo picked without another API
    # roundtrip. Keeps the bundle self-contained.
    config_lines = [
        "# Generated by Ship — install bundle",
        "# Learn more: https://app.ship.elmundi.com",
        f"preset: {preset_id}",
        "version: 1",
    ]
    if repo_full_name:
        config_lines.append(f"repo: {repo_full_name}")
    if included_kinds:
        config_lines.append("lanes:")
        for kind in included_kinds:
            config_lines.append(f"  - {kind}")
    files.append((".ship/config.yml", "\n".join(config_lines) + "\n"))
    return files


# ---------------------------------------------------------------------------
# Knowledge starters — one-shot seed for ``.ship/knowledge/*.md`` buckets.
#
# Unlike presets, knowledge buckets never live in the Ship backend DB;
# the tenant commits plain markdown files into its own repo and Ship's
# knowledge lister scans them on read. "Seeding" therefore means:
# open a PR that drops a starter template at the conventional path.
# The tenant reviews + merges the PR exactly like any other bundle; if
# the file already exists on the default branch the seed becomes a
# no-op (path stays the same, content gets overwritten only if the
# tenant hand-rebases — we never force-push over their edits).
#
# Sources live under ``artifacts/knowledge-starters/<slug>.md`` so the
# content is part of the same artifact catalog that ``ship_artifact_check``
# vets. Keep the slug list in lockstep with the wizard's checkbox labels.
# ---------------------------------------------------------------------------

KNOWLEDGE_STARTERS: Final[tuple[str, ...]] = ("code-style", "ui-runbook")


def knowledge_starter_files(
    selection: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(path, content)`` tuples for the selected knowledge starters.

    ``selection`` filters ``KNOWLEDGE_STARTERS``; ``None`` means "seed
    everything". Unknown slugs raise :class:`CatalogError` so a stale
    UI can't silently drop a checkbox.

    Each starter source lives at
    ``artifacts/knowledge-starters/<slug>.md`` and is installed at
    ``.ship/knowledge/<slug>.md`` — the exact shape
    :mod:`backend.app.services.knowledge_lister` scans for.
    """
    if selection is None:
        chosen: list[str] = list(KNOWLEDGE_STARTERS)
    else:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in selection:
            slug = raw.strip()
            if not slug or slug in seen:
                continue
            if slug not in KNOWLEDGE_STARTERS:
                raise CatalogError(
                    f"Unknown knowledge starter {slug!r}. "
                    f"Expected one of: {sorted(KNOWLEDGE_STARTERS)}"
                )
            cleaned.append(slug)
            seen.add(slug)
        chosen = cleaned

    root = ARTIFACTS_ROOT / "knowledge-starters"
    out: list[tuple[str, str]] = []
    for slug in chosen:
        source = root / f"{slug}.md"
        try:
            content = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(
                f"Knowledge starter {slug!r} is missing on disk ({source}): {exc}"
            ) from exc
        out.append((f".ship/knowledge/{slug}.md", content))
    return out
