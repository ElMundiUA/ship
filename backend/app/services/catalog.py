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
from collections import OrderedDict
from collections.abc import Iterable, Mapping
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
    "list_specialist_role_patterns",
    "list_knowledge_recipe_patterns",
    "list_patterns_by_mode",
    "list_patterns_for_workspace",
    "list_patterns_by_mode_for_workspace",
    "custom_pattern_to_artifact",
    "resolve_lane_workflow",
    "list_tools",
    "KNOWLEDGE_STARTERS",
    "knowledge_starter_slugs",
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

_STATIC_KNOWLEDGE_STARTERS: Final[tuple[str, ...]] = ("code-style", "ui-runbook")
_SHIP_RECIPE_STARTER_PREFIX: Final[str] = "ship-recipes/"
_ROLE_PATTERN_PREFIX: Final[str] = "role-"
_KNOWLEDGE_RECIPE_PREFIXES: Final[tuple[str, ...]] = (
    "common-",
    "flow-",
    "onboard-",
    "op-",
    "scan-",
)
_KNOWLEDGE_RECIPE_CATEGORIES: Final[set[str]] = {
    "common",
    "flow",
    "onboard",
    "op",
    "scan",
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

    # ------------------------------------------------------------------
    # RFC-0008 metadata (category / modes / trigger / inputs).
    #
    # Fields are optional at the frontmatter layer so pre-RFC artifacts
    # keep loading. The loader never invents values — ``None`` means
    # "legacy artifact, the caller should decide the default". Rename
    # phase (RFC-0008 §Phase 1) populates them for every pattern.
    # ------------------------------------------------------------------

    @property
    def category(self) -> str | None:
        """One of role|flow|scan|op|onboard|sys, or ``None`` for legacy."""
        value = self.spec.get("category")
        return value if isinstance(value, str) and value else None

    @property
    def modes(self) -> list[str]:
        """Invocation modes: subset of ``{"lane", "request"}``.

        Empty list = the pattern isn't attachable (``sys-*`` helpers).
        ``None`` in frontmatter = legacy artifact; callers should
        default to ``["lane", "request"]`` during the transition
        unless the pattern's original group says otherwise.
        """
        raw = self.spec.get("modes")
        if not isinstance(raw, (list, tuple)):
            return []
        valid = {"lane", "request"}
        return [m for m in raw if isinstance(m, str) and m in valid]

    @property
    def default_trigger(self) -> dict[str, Any] | None:
        """Lane wiring hint (event/schedule). ``None`` for request-only."""
        raw = self.spec.get("default_trigger")
        return dict(raw) if isinstance(raw, dict) else None

    @property
    def include(self) -> list[str]:
        """``common-*`` pattern ids to prepend at render time."""
        raw = self.spec.get("include")
        if not isinstance(raw, (list, tuple)):
            return []
        return [s for s in raw if isinstance(s, str) and s]

    @property
    def lane_workflow(self) -> str | None:
        """Explicit starter YAML id from frontmatter (override).

        When absent, the effective starter is computed by
        :func:`resolve_lane_workflow`, which falls back to
        :attr:`category` + :attr:`default_trigger`.
        """
        raw = self.spec.get("lane_workflow")
        return raw if isinstance(raw, str) and raw else None

    # ------------------------------------------------------------------
    # RFC-0008 C3.3 — lane identity.
    #
    # Patterns that back one of the built-in seeded lanes declare a
    # stable ``lane_id`` slug (e.g. ``pr_review``) so the seeder /
    # dashboard can keep a human-friendly lane name that's decoupled
    # from the pattern's own id. The pattern id stays the authoritative
    # *content* identifier; the lane_id is the authoritative *runtime*
    # identifier (``Pipeline.lane_id`` in the DB).
    #
    # Absent → pattern id doubles as lane id (Phase 2 / C5 patterns
    # follow this convention).
    # ------------------------------------------------------------------

    @property
    def lane_id(self) -> str | None:
        """Stable lane slug this pattern participates in, or ``None``.

        Multiple patterns can declare the same ``lane_id`` — the
        lane-recipe builder merges them into one multi-pattern lane
        (RFC-0008 C3.2).
        """
        raw = self.spec.get("lane_id")
        return raw if isinstance(raw, str) and raw else None

    @property
    def lane_name(self) -> str | None:
        """Human-readable lane label (Library card title / Dashboard row)."""
        raw = self.spec.get("lane_name")
        return raw if isinstance(raw, str) and raw else None

    @property
    def lane_summary(self) -> str | None:
        """One-line Library tagline. Falls back to ``description`` if absent."""
        raw = self.spec.get("lane_summary")
        return raw if isinstance(raw, str) and raw else None

    @property
    def inputs(self) -> list[dict[str, Any]]:
        """Named parameters the Requests form should render."""
        raw = self.spec.get("inputs")
        if not isinstance(raw, (list, tuple)):
            return []
        cleaned: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                cleaned.append(dict(item))
        return cleaned

    @property
    def enabled_on_install(self) -> dict[str, Any]:
        """Which presets turn this lane on by default at seed time."""
        raw = self.spec.get("enabled_on_install")
        if not isinstance(raw, dict):
            return {}
        return dict(raw)

    def enabled_for_preset(self, preset_id: str | None) -> bool:
        """Resolve ``enabled_on_install`` against a preset id.

        Unknown preset or missing metadata → fall back to the
        ``default`` key (then to ``False``). Phase-2 migration reads
        this to project patterns onto seed bundle lanes.
        """
        spec = self.enabled_on_install
        if not spec:
            return False
        presets = spec.get("presets")
        if preset_id and isinstance(presets, dict):
            value = presets.get(preset_id)
            if isinstance(value, bool):
                return value
        default = spec.get("default")
        return bool(default) if isinstance(default, bool) else False

    @property
    def source(self) -> str:
        """``"builtin"`` for filesystem patterns, ``"workspace"`` for DB rows.

        Set by :func:`custom_pattern_to_artifact` when promoting a
        :class:`~backend.app.db.models.custom_patterns.CustomPattern`;
        baked-in patterns never populate this frontmatter key so the
        fallback stays ``builtin``.
        """
        raw = self.raw.get("source")
        return raw if isinstance(raw, str) and raw else "builtin"

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
            # RFC-0008 metadata (Draft). ``None``/``[]`` on pre-RFC
            # artifacts; the Library/Requests UIs treat missing values
            # as legacy defaults.
            "category": self.category,
            "modes": self.modes,
            "default_trigger": self.default_trigger,
            "lane_workflow": self.lane_workflow,
            "resolved_lane_workflow": resolve_lane_workflow(self),
            "lane_id": self.lane_id,
            "lane_name": self.lane_name,
            "lane_summary": self.lane_summary,
            "include": list(self.include),
            "inputs": list(self.inputs),
            "enabled_on_install": self.enabled_on_install,
            "source": self.source,
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


def _is_specialist_role_pattern(entry: CatalogArtifact) -> bool:
    return entry.id.startswith(_ROLE_PATTERN_PREFIX) or entry.category == "role"


def _is_knowledge_recipe_pattern(entry: CatalogArtifact) -> bool:
    if _is_specialist_role_pattern(entry):
        return False
    if entry.id.startswith(_KNOWLEDGE_RECIPE_PREFIXES):
        return True
    return entry.category in _KNOWLEDGE_RECIPE_CATEGORIES


def list_specialist_role_patterns() -> list[CatalogArtifact]:
    """Pattern artifacts that now back the Specialist template catalog."""
    return [entry for entry in list_patterns() if _is_specialist_role_pattern(entry)]


def list_knowledge_recipe_patterns() -> list[CatalogArtifact]:
    """Procedural catalog entries exposed as seedable knowledge recipes."""
    return [entry for entry in list_patterns() if _is_knowledge_recipe_pattern(entry)]


def list_tools() -> list[CatalogArtifact]:
    return _load_kind("tool")


def list_collections() -> list[CatalogArtifact]:
    return _load_kind("collection")


# ---------------------------------------------------------------------------
# RFC-0008 mode/workflow resolution helpers
# ---------------------------------------------------------------------------


def list_patterns_by_mode(mode: str) -> list[CatalogArtifact]:
    """Patterns that advertise ``mode`` in their ``spec.modes``.

    Non-RFC-0008 patterns (legacy ids, no ``modes`` declared) are
    treated as ``[lane, request]`` so the Library / Requests UIs keep
    rendering them during the transition.
    """
    if mode not in {"lane", "request"}:
        raise ValueError(f"unknown pattern mode: {mode!r}")
    out: list[CatalogArtifact] = []
    for entry in list_patterns():
        declared = entry.modes
        # Legacy (pre-RFC-0008) patterns have neither ``category`` nor
        # ``modes`` set. Default to both modes until Phase 1 rename
        # populates every pattern.
        if not declared and entry.category is None:
            out.append(entry)
            continue
        if mode in declared:
            out.append(entry)
    return out


def resolve_lane_workflow(entry: CatalogArtifact) -> str | None:
    """Pick a starter YAML id for a pattern about to be wired as a lane.

    Priority (first non-None wins):

    1. Explicit ``spec.lane_workflow`` in the pattern's frontmatter.
    2. ``pr-and-ci-gate`` when ``default_trigger.event`` is
       ``pull_request`` / ``pull_request_target`` — these need PR
       permissions and the special comment-posting reporter.
    3. ``pipeline-self-heal`` for ``op-workflow-*`` patterns (workflow
       rewriting needs ``actions: write``).
    4. ``parallel-audit-lanes`` for ``category: scan`` (matrix fan-out
       reporter).
    5. ``scheduled-sdlc-lane`` — the universal agent-run path. Covers
       ``role`` / ``flow`` / most ``op`` / ``onboard`` / non-matrix
       ``scan``.

    Returns ``None`` when the pattern isn't lane-attachable at all
    (``modes`` empty — ``common-*`` patterns).
    """
    if "lane" not in entry.modes and entry.category is not None:
        return None
    explicit = entry.lane_workflow
    if explicit:
        return explicit
    trigger = entry.default_trigger or {}
    event = trigger.get("event")
    if isinstance(event, str) and event.startswith("pull_request"):
        return "pr-and-ci-gate"
    if entry.id.startswith("op-workflow-"):
        return "pipeline-self-heal"
    if entry.category == "scan":
        return "parallel-audit-lanes"
    return "scheduled-sdlc-lane"


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


def starter_workflow_for_pattern(pattern_key: str) -> str | None:
    """Return the starter workflow id a pattern would install when wired as a lane.

    Wraps :func:`resolve_lane_workflow` with the pattern lookup so
    callers (P5-05 ``compose_seed_files`` / ``synthetic_lane_sync``)
    don't have to walk the catalog themselves. Returns ``None`` when:

    - the pattern id isn't known to the catalog (typo / yanked
      pattern),
    - the pattern doesn't declare ``modes: [lane, ...]`` (i.e. it's
      request-only, like ``onboard-seed-knowledge``),
    - or :func:`resolve_lane_workflow` itself returned ``None``.

    Pure: no I/O, no DB; cached by :func:`list_patterns`.
    """
    for entry in list_patterns():
        if entry.id == pattern_key:
            return resolve_lane_workflow(entry)
    return None


def bundle_lane_entries(
    bundle: "Iterable[str]",
) -> "OrderedDict[str, dict[str, object]]":
    """Project a flat ``bundle`` of pattern keys into the lanes mapping.

    The result is the exact shape :func:`emit_config_yaml` accepts as
    its ``lanes`` argument — one entry per pattern in ``bundle`` that
    declares ``lane`` mode AND a parseable ``default_trigger``.
    Patterns without a lane mode (request-only templates like
    ``onboard-seed-knowledge``) are silently skipped — they still
    belong in the bundle for accounting (the wizard records them in
    its v2 marker) but they don't need a lane row.

    Lane id derivation:

    1. ``spec.lane_id`` (when set) — preserves the recipe's symbolic
       slug (``pr_review``, ``self_heal``).
    2. Pattern id (with dots collapsed to underscores) — falls back
       to a slug that matches the pattern key, so a freshly-authored
       pattern produces a usable lane row without a code change.

    Output ordering follows the input bundle (``OrderedDict``) so the
    emitted YAML mirrors the bundle's recommended display order.
    """
    from collections import OrderedDict

    from backend.app.services.lane_recipes import _flatten_default_trigger

    by_id = {entry.id: entry for entry in list_patterns()}
    lanes: "OrderedDict[str, dict[str, object]]" = OrderedDict()
    for pattern_key in bundle:
        entry = by_id.get(pattern_key)
        if entry is None:
            continue
        if "lane" not in entry.modes and entry.category is not None:
            continue
        trigger = _flatten_default_trigger(entry.default_trigger)
        if trigger is None:
            continue
        lane_id = entry.lane_id or pattern_key.replace(".", "_")
        # Stable lane row: the pattern slug doubles as a key into
        # the catalog so ``Lane.pattern`` joins remain free.
        lane_entry: dict[str, object] = dict(trigger)
        lane_entry.setdefault("pattern", entry.id)
        lanes[lane_id] = lane_entry
    return lanes


def emit_config_yaml_for_bundle(
    bundle: "Iterable[str]",
    *,
    repo_full_name: str | None = None,
    preset_id: str | None = "default",
) -> str:
    """Render ``.ship/config.yml`` directly from a bundle of pattern keys.

    Wave-8 wizard path: the canonical bundle (``DEFAULT_BUNDLE``) is
    the only input — no per-preset branching. Internally calls
    :func:`bundle_lane_entries` to derive the ``lanes:`` mapping
    and :func:`emit_config_yaml` to serialise (so the YAML preamble,
    quoting, and key ordering stay byte-identical to the editor
    path that powers ``POST .../config/propose``).
    """
    lanes = bundle_lane_entries(bundle)
    return emit_config_yaml(
        preset_id=preset_id,
        repo_full_name=repo_full_name,
        lanes=lanes,
        process=default_development_process_config(),
    )


def emit_config_yaml(
    *,
    preset_id: str | None,
    repo_full_name: str | None,
    lanes: "Mapping[str, Mapping[str, object]]",
    process: "Mapping[str, object] | None" = None,
) -> str:
    """Serialize a ``.ship/config.yml`` body in schema v2.

    Used by two write paths:

    - ``preset_bundle_files`` — the wizard seed / install-bundle flow,
      which derives ``lanes`` from :data:`lane_recipes.LaneRecipe` records.
    - ``POST /v1/.../repos/{id}/config/propose`` — the Library editor
      in the console, which receives an already-edited mapping from the
      operator and opens a single-file PR.

    Factored out so the editor path can round-trip identical YAML to
    what the seed path emits (same preamble, same quoting, same key
    ordering) — the diff view in the console is only useful if the
    "unchanged" bytes are actually byte-identical.

    Keys inside each lane preserve the input mapping order. The caller
    is expected to have validated that exactly one of ``event`` /
    ``schedule`` / ``once`` is present per lane; this function is
    intentionally dumb about semantics and only handles YAML quoting.
    """
    lines: list[str] = [
        "# Generated by Ship — install bundle",
        "# Learn more: https://app.ship.elmundi.com",
    ]
    if preset_id:
        lines.append(f"preset: {preset_id}")
    lines.append("version: 2")
    lines.append("shipctl_min: 0.12.0")
    if repo_full_name:
        lines.append(f"repo: {repo_full_name}")
    lines.extend(
        [
            "api:",
            '  base_url: "https://ship.elmundi.com"',
            "  channel: stable",
            "  ttl_hours: 24",
            "  offline_ok: true",
            "stack:",
            "  tracker: none",
            "  ci: gh-actions",
            "  agents: []",
            "  language: multi",
            "agent:",
            "  default: {}",
            "  overrides: {}",
        ]
    )

    if process:
        lines.append("process:")
        dumped = yaml.safe_dump(
            dict(process),
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=False,
        ).rstrip()
        for line in dumped.splitlines():
            lines.append(f"  {line}" if line else "")

    if lanes:
        lines.append("lanes:")
        for lane_id, trigger in lanes.items():
            lines.append(f"  {lane_id}:")
            for key, value in _normalise_lane_for_config(trigger).items():
                # ``patterns: [ids]`` is the only list-typed key we
                # currently emit (RFC-0008 C3.1 multi-pattern lanes).
                # Render it in flow-style so the line stays compact
                # and the diff against the previous ``pattern: <id>``
                # form is a single-line swap.
                if isinstance(value, (list, tuple)):
                    inner = ", ".join(_render_yaml_scalar(v) for v in value)
                    lines.append(f"    {key}: [{inner}]")
                else:
                    lines.append(f"    {key}: {_render_yaml_scalar(value)}")

    return "\n".join(lines) + "\n"


def default_development_process_config() -> dict[str, object]:
    """Return the minimal FSM process seeded into new repo configs.

    This is intentionally product-language first. Runtime ``lanes:`` remain
    below it as the compatibility layer until the executor reads ``process:``
    directly.
    """

    return {
        "id": "development",
        "name": "Development Process",
        "primary": True,
        "states": [
            {
                "id": "task_intake",
                "name": "Intake",
                "specialist": {"id": "intake", "name": "Intake specialist"},
                "layout": {"x": 72, "y": 170},
                "instructions": (
                    "Clarify the request, collect missing context, and decide "
                    "whether the task is ready for requirements."
                ),
            },
            {
                "id": "ba_requirements",
                "name": "Requirements",
                "specialist": {"id": "business_analyst", "name": "Business analyst"},
                "layout": {"x": 338, "y": 170},
                "instructions": (
                    "Turn the request into acceptance criteria, constraints, "
                    "risks, and open questions."
                ),
            },
            {
                "id": "dev_implementation",
                "name": "Implementation",
                "specialist": {"id": "developer", "name": "Developer"},
                "layout": {"x": 604, "y": 170},
                "instructions": (
                    "Implement the change, update tests and documentation, and "
                    "prepare the work for review."
                ),
            },
            {
                "id": "qa_manual",
                "name": "Quality Review",
                "specialist": {"id": "qa_engineer", "name": "QA engineer"},
                "layout": {"x": 870, "y": 170},
                "instructions": (
                    "Validate acceptance criteria, edge cases, and user-facing "
                    "quality before release."
                ),
            },
            {
                "id": "pr_review",
                "name": "Final Review",
                "specialist": {"id": "review_owner", "name": "Review owner"},
                "layout": {"x": 1136, "y": 170},
                "instructions": (
                    "Review the completed work for correctness, maintainability, "
                    "scope, and release readiness."
                ),
            },
        ],
        "transitions": [
            {"from": "task_intake", "to": "ba_requirements"},
            {"from": "ba_requirements", "to": "dev_implementation"},
            {"from": "dev_implementation", "to": "qa_manual"},
            {"from": "qa_manual", "to": "pr_review"},
        ],
        "routines": [],
    }


def _normalise_lane_for_config(trigger: "Mapping[str, object]") -> dict[str, object]:
    """Render legacy flat lane triggers as current CLI schema v2 lanes."""
    out: dict[str, object] = {}
    if isinstance(trigger.get("kind"), str):
        kind = str(trigger["kind"])
    elif "once" in trigger:
        kind = "once"
    elif "event" in trigger:
        kind = "event"
    elif "schedule" in trigger:
        kind = "schedule"
    else:
        kind = ""

    if kind:
        out["kind"] = kind
    if kind == "event":
        event = trigger.get("on", trigger.get("event"))
        if event is not None:
            out["on"] = event
    elif kind == "schedule":
        cron = trigger.get("cron", trigger.get("schedule"))
        if cron is not None:
            out["cron"] = cron
    elif kind == "once":
        once = trigger.get("once")
        if once is not None:
            out["once"] = once

    for key in ("pattern", "patterns", "pattern_version", "fanout", "permissions", "runner", "timeout_minutes", "concurrency"):
        if key in trigger:
            out[key] = trigger[key]

    if kind == "once":
        idem = trigger.get("idempotency")
        idem_key = trigger.get("idempotency_key")
        if isinstance(idem, dict):
            out["idempotency"] = idem
        elif isinstance(idem_key, str) and idem_key:
            out["idempotency"] = {
                "key": idem_key,
                "store": "file",
                "reset_on": "version-change",
            }
    return out


def _render_yaml_scalar(value: object) -> str:
    """Best-effort scalar renderer matching the legacy inline emitter.

    Quotes strings that contain whitespace or YAML-significant chars
    (``*``, ``{``, ``}``, ``/``, ``+``) so schedule crons and
    idempotency key templates don't get mis-parsed. Keeps ints/bools
    bare so the output matches what PyYAML round-trips.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in (" ", "*", "{", "}", "/", "+")):
        return f'"{text}"'
    return text


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
    # Lazy imports keep the module import graph flat and also avoid a
    # cycle (lane_recipes pulls pattern catalog metadata which lives in
    # this module).
    from backend.app.services import starter_workflows
    from backend.app.services.lane_recipes import (
        list_lane_recipes,
        resolve_enabled_lane_ids,
    )

    enabled_ids = resolve_enabled_lane_ids(preset_id)
    files: list[tuple[str, str]] = []
    included_recipes: list = []
    for recipe in list_lane_recipes():
        if recipe.lane_id not in enabled_ids:
            continue
        entry = starter_workflows.get(recipe.workflow_id)
        if entry is None:
            continue
        content = entry.read_yaml()
        if not content:
            continue
        files.append((entry.install_target, content))
        included_recipes.append(recipe)

    # .ship/config.yml — config schema v2. Emits ``lanes:`` as a
    # mapping so ``lanes_sync`` can parse trigger/pattern/idempotency
    # per-lane. Resolver-only recipes (``trigger is None``, e.g.
    # ``code_map``) are skipped. Multi-pattern recipes emit the
    # ``patterns: [ids]`` list (RFC-0008 C3.1) and fan-out mode
    # (C3.2); single-pattern recipes emit the scalar ``pattern: <id>``
    # form so diffs against older bundles stay minimal.
    lanes_map: dict[str, Mapping[str, object]] = {}
    for recipe in included_recipes:
        if recipe.trigger is None:
            continue
        entry_map: dict[str, object] = dict(recipe.trigger)
        if len(recipe.patterns) >= 2:
            entry_map["patterns"] = list(recipe.patterns)
            if recipe.fanout != "matrix":
                entry_map["fanout"] = recipe.fanout
        elif len(recipe.patterns) == 1:
            entry_map.setdefault("pattern", recipe.patterns[0])
        lanes_map[recipe.lane_id] = entry_map
    yaml_body = emit_config_yaml(
        preset_id=preset_id,
        repo_full_name=repo_full_name,
        lanes=lanes_map,
    )
    files.append((".ship/config.yml", yaml_body))
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
# Static sources live under ``artifacts/knowledge-starters/<slug>.md``.
# Ship recipe starters are generated from procedural ``artifacts/patterns``
# entries so we can split the catalog without duplicating dozens of legacy
# prompt bodies. The generated articles intentionally use the native knowledge
# shape first, then carry the old prompt text under "Legacy recipe body" until
# the editorial rewrite pass lands.
# ---------------------------------------------------------------------------


def knowledge_starter_slugs() -> tuple[str, ...]:
    """Return every seedable knowledge starter slug in deterministic order."""
    recipe_slugs = tuple(
        f"{_SHIP_RECIPE_STARTER_PREFIX}{entry.id}"
        for entry in list_knowledge_recipe_patterns()
    )
    return (*_STATIC_KNOWLEDGE_STARTERS, *recipe_slugs)


KNOWLEDGE_STARTERS: Final[tuple[str, ...]] = knowledge_starter_slugs()


def knowledge_starter_files(
    selection: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(path, content)`` tuples for the selected knowledge starters.

    ``selection`` filters :func:`knowledge_starter_slugs`; ``None`` means "seed
    everything". Unknown slugs raise :class:`CatalogError` so a stale
    UI can't silently drop a checkbox.

    Static starter sources live at ``artifacts/knowledge-starters/<slug>.md``.
    Recipe starters are generated from procedural pattern artifacts and
    installed under ``.ship/knowledge/ship-recipes/<pattern-id>.md``.
    """
    known = knowledge_starter_slugs()
    known_set = set(known)
    if selection is None:
        chosen: list[str] = list(known)
    else:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in selection:
            slug = raw.strip()
            if not slug or slug in seen:
                continue
            if slug not in known_set:
                raise CatalogError(
                    f"Unknown knowledge starter {slug!r}. "
                    f"Expected one of: {sorted(known)}"
                )
            cleaned.append(slug)
            seen.add(slug)
        chosen = cleaned

    root = ARTIFACTS_ROOT / "knowledge-starters"
    recipes_by_slug = {
        f"{_SHIP_RECIPE_STARTER_PREFIX}{entry.id}": entry
        for entry in list_knowledge_recipe_patterns()
    }
    out: list[tuple[str, str]] = []
    for slug in chosen:
        recipe = recipes_by_slug.get(slug)
        if recipe is not None:
            content = _render_recipe_knowledge_article(recipe)
        else:
            source = root / f"{slug}.md"
            try:
                content = source.read_text(encoding="utf-8")
            except OSError as exc:
                raise CatalogError(
                    f"Knowledge starter {slug!r} is missing on disk ({source}): {exc}"
                ) from exc
        out.append((f".ship/knowledge/{slug}.md", content))
    return out


def _render_recipe_knowledge_article(entry: CatalogArtifact) -> str:
    """Render a procedural pattern as a first-class knowledge article."""
    title = entry.name or entry.id
    tags = ", ".join(f"`{tag}`" for tag in entry.tags) if entry.tags else "`none`"
    category = entry.category or entry.group or "recipe"
    source = f"pattern/{entry.id}"
    recommended_tools = _recommended_tools_for_recipe(entry)
    lines: list[str] = [
        f"# {title}",
        "",
        "## Metadata",
        "",
        f"- Source: `{source}`",
        f"- Legacy slug: `{entry.id}`",
        f"- Category: `{category}`",
        f"- Tags: {tags}",
        f"- Version: `{entry.version or 'unknown'}`",
        "",
        "## When To Use",
        "",
        entry.description or "Use this recipe when its title, tags, or source task match the current work.",
        "",
        "## Inputs",
        "",
        _render_recipe_inputs(entry),
        "",
        "## Recommended Tools",
        "",
        *[f"- {tool}" for tool in recommended_tools],
        "",
        "These tools are recommendations for Ship-controlled orchestration. They do not grant direct tracker writes, undeclared FSM transitions, unaudited side effects, or direct repository pushes.",
        "",
        "## Steps",
        "",
        "Start from the legacy recipe body below. During the editorial rewrite pass, promote the useful instructions into concise knowledge-article steps.",
        "",
        "## Checks",
        "",
        "- Confirm the selected recipe actually applies to the ticket or technical question.",
        "- Prefer existing repository and workspace knowledge before inventing a new approach.",
        "- Record material actions through the Ship audit path.",
        "",
        "## Risks",
        "",
        "- Legacy pattern text may mention direct tracker, PR, or git actions. Treat those as desired intents; Ship must still enforce configured transitions, PR-only repository writes, and audit logging.",
        "",
        "## Related Policies",
        "",
        "- Workspace policies injected by Ship remain mandatory and override this recipe.",
        "",
        "## Legacy Recipe Body",
        "",
        entry.body.strip(),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_recipe_inputs(entry: CatalogArtifact) -> str:
    inputs = entry.inputs
    if not inputs:
        return "- Relevant ticket or request context.\n- Repository/workspace context needed to evaluate the recipe."
    lines: list[str] = []
    for item in inputs:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        input_type = str(item.get("type") or "string")
        required = "required" if item.get("required") else "optional"
        hint = str(item.get("hint") or "").strip()
        suffix = f" - {hint}" if hint else ""
        lines.append(f"- `{name}` ({input_type}, {required}){suffix}")
    return "\n".join(lines) if lines else "- Relevant ticket or request context."


def _recommended_tools_for_recipe(entry: CatalogArtifact) -> list[str]:
    tools = [
        "`knowledge_search_v2` or workspace knowledge search before inventing a solution",
    ]
    category = entry.category or entry.group or ""
    tags = {str(tag).lower() for tag in entry.tags}
    if category == "scan" or entry.id.startswith("scan-"):
        tools.append("Repository/code search and CI evidence readers for concrete findings")
    if "pr" in tags or "review" in tags or "pull_request" in repr(entry.default_trigger):
        tools.append("Pull request readers for diff, checks, comments, and linked ticket context")
    if "docs" in tags or "runbook" in tags:
        tools.append("Repository file readers for docs and runbook verification")
    if "security" in tags or "iam" in tags or "pii" in tags:
        tools.append("Secret, dependency, and policy evidence readers where configured")
    tools.append("Ship callback/audit reporting for outcomes, escalations, and handoffs")
    return tools


# ---------------------------------------------------------------------------
# Workspace-private catalog layer (RFC-0008 §H — PR-6)
# ---------------------------------------------------------------------------
#
# Workspace admins can author catalog patterns at runtime via the
# Console's AI author modal (or Navigator, or hand-crafted JSON).
# Those rows live in ``custom_patterns`` and need to appear in every
# pattern picker the baked-in catalog already feeds.
#
# The adapter below promotes a DB row to an in-memory
# :class:`CatalogArtifact` by synthesising the frontmatter dict the
# constructor expects. The merge helpers then overlay the workspace
# rows on top of :func:`list_patterns` — collisions resolve in favour
# of the workspace-private entry so operators can shadow a baked-in
# pattern without forking Ship.


def custom_pattern_to_artifact(row: Any) -> CatalogArtifact:
    """Adapt a :class:`CustomPattern` DB row into a :class:`CatalogArtifact`.

    Kept as a standalone function (rather than a classmethod on the
    ORM model) so ``backend.app.services.catalog`` stays import-clean
    for callers that only know about the filesystem catalog.
    """
    spec: dict[str, Any] = dict(row.spec or {})
    # Mirror the frontmatter contract: ``modes`` and ``inputs`` always
    # live under ``spec`` in baked-in patterns, so we fold the
    # structured columns back in before handing the dict to the
    # :class:`CatalogArtifact` constructor.
    spec.setdefault("modes", list(row.modes or []))
    spec.setdefault("inputs", list(row.inputs or []))
    if row.category is not None:
        spec.setdefault("category", row.category)
    meta: dict[str, Any] = {
        "id": row.pattern_id,
        "name": row.name or row.pattern_id,
        "description": row.description or "",
        "spec": spec,
        "updated_at": row.updated_at,
        # Marker so downstream code can distinguish workspace-private
        # rows from baked-in patterns (e.g. the Console surfaces a
        # "custom" badge and exposes a delete action only on these).
        "source": "workspace",
    }
    # ``source_path`` is required by the constructor but irrelevant
    # for DB-backed rows; synthesise a sentinel so any ``str(path)``
    # call stays safe.
    sentinel = Path(f"<custom:{row.workspace_id}:{row.pattern_id}>")
    return CatalogArtifact(
        kind="pattern", meta=meta, body=row.body or "", source_path=sentinel
    )


def _merge_custom(
    base: list[CatalogArtifact], custom: list[CatalogArtifact]
) -> list[CatalogArtifact]:
    """Overlay ``custom`` on top of ``base`` by artifact id.

    Workspace-private entries win on collision — operators can shadow
    a baked-in pattern without having to pick a different id. Order
    is stable: base entries keep their position, collisions swap the
    value in place, and brand-new workspace entries are appended.
    """
    if not custom:
        return list(base)
    by_id: dict[str, CatalogArtifact] = {}
    order: list[str] = []
    for entry in base:
        by_id[entry.id] = entry
        order.append(entry.id)
    for entry in custom:
        if entry.id not in by_id:
            order.append(entry.id)
        by_id[entry.id] = entry
    return [by_id[pid] for pid in order]


def list_patterns_for_workspace(
    custom_rows: list[Any],
) -> list[CatalogArtifact]:
    """Baked-in catalog plus workspace-private rows, merged.

    Callers are expected to load ``custom_rows`` via SQLAlchemy
    (async) and pass them in — we keep this helper sync so it stays
    callable from the same code paths that use :func:`list_patterns`.
    """
    custom_entries = [custom_pattern_to_artifact(r) for r in custom_rows]
    return _merge_custom(list_patterns(), custom_entries)


def list_patterns_by_mode_for_workspace(
    mode: str, custom_rows: list[Any]
) -> list[CatalogArtifact]:
    """Mode-filtered variant of :func:`list_patterns_for_workspace`.

    Mirrors the legacy-pattern fallback logic in
    :func:`list_patterns_by_mode` — patterns without declared
    ``modes`` *and* without a ``category`` slot are treated as
    attachable in both modes so nothing silently disappears mid-
    RFC-0008 transition.
    """
    if mode not in {"lane", "request"}:
        raise ValueError(f"unknown pattern mode: {mode!r}")
    merged = list_patterns_for_workspace(custom_rows)
    out: list[CatalogArtifact] = []
    for entry in merged:
        declared = entry.modes
        if not declared and entry.category is None:
            out.append(entry)
            continue
        if mode in declared:
            out.append(entry)
    return out
