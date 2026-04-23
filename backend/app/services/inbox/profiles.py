"""Inbox profile catalog + resolver (RFC-0010 P2-11).

The catalog (``profile_catalog.yaml`` next to this module) defines the
nine reusable inbox-emit profiles every user-facing pattern picks
from. A profile encodes, for each of the five inbox types
(``clarification``, ``improvement``, ``failure``, ``approval``,
``exception``), whether the pattern emits an item and which symbolic
``handle`` should own it once the workspace routing layer resolves it
to a concrete user/group.

Two layers compose into the final per-pattern rule set:

- **Catalog layer** (this file + YAML): profiles are shared across
  many patterns, edited rarely, and reviewed centrally.
- **Pattern layer** (`spec.inbox.overrides` in each ARTIFACT.md):
  the pattern can override a single inbox type's ``handle``,
  ``enabled``, or ``when`` without forking the whole profile.

Profiles may also chain via an ``inherits:`` key (depth-first,
shallow merge) so closely-related profiles like ``scan_with_autofix``
can extend ``scan_default`` instead of duplicating it.

This module is pure: no DB, no async, no network. It loads + caches
the YAML once and exposes :func:`resolve_profile` /
:func:`resolve_for_pattern` for the intake service.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)


INBOX_TYPES: tuple[str, ...] = (
    "clarification",
    "improvement",
    "failure",
    "approval",
    "exception",
)

# Meta-keys that may appear inside a profile body but are NOT inbox
# types — keep this list in sync with the YAML schema. The validator +
# resolver both filter against it before iterating per-type rules.
_PROFILE_META_KEYS: frozenset[str] = frozenset({"inherits"})

_DEFAULT_CATALOG_PATH: pathlib.Path = (
    pathlib.Path(__file__).parent / "profile_catalog.yaml"
)

_SILENT_PROFILE_NAME = "silent"

# Module-level cache. Keyed by absolute path so tests that point at a
# fixture file don't accidentally collide with the shipped catalog.
_CATALOG_CACHE: dict[pathlib.Path, dict[str, dict]] = {}


class ProfileCatalogError(ValueError):
    """Raised when the catalog YAML is malformed or a resolution fails."""


@dataclass(frozen=True)
class EmitRule:
    """Resolved rule for a single inbox type after profile + override merge.

    ``enabled=False`` means the pattern never emits this inbox type.
    ``handle`` is the symbolic role name (workspace routing rules
    resolve it to a target user/group/strategy at intake time).
    ``when`` is the catalog-defined list of conditions that the intake
    service compares against the pipeline run's outcome to decide
    whether to actually create the item.
    """

    type: str
    enabled: bool
    handle: str | None
    when: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedProfile:
    """The full set of emit rules for one pattern, keyed by inbox type."""

    profile_name: str
    rules: dict[str, EmitRule]


# ---------------------------------------------------------------------------
# Loading + validation
# ---------------------------------------------------------------------------


def _validate_catalog(raw: object) -> dict[str, dict]:
    """Run structural checks on the parsed YAML and return the inner map.

    Raises :class:`ProfileCatalogError` with a pointed message on every
    schema violation we know how to detect at load time. Catching
    these here means downstream callers can assume any profile name
    they look up either resolves cleanly or hits a clear error.
    """
    if not isinstance(raw, dict):
        raise ProfileCatalogError(
            "catalog YAML must be a mapping at the top level"
        )
    profiles = raw.get("inbox_profiles")
    if not isinstance(profiles, dict):
        raise ProfileCatalogError(
            "catalog YAML must contain a top-level `inbox_profiles` mapping"
        )

    known_names = set(profiles.keys())
    for name, body in profiles.items():
        if not isinstance(name, str) or not name:
            raise ProfileCatalogError(
                f"profile name must be a non-empty string (got {name!r})"
            )
        if not isinstance(body, dict):
            raise ProfileCatalogError(
                f"profile `{name}` body must be a mapping"
            )
        inherits = body.get("inherits")
        if inherits is not None:
            if not isinstance(inherits, str):
                raise ProfileCatalogError(
                    f"profile `{name}`: `inherits` must be a string"
                )
            if inherits not in known_names:
                raise ProfileCatalogError(
                    f"profile `{name}` inherits from unknown profile "
                    f"`{inherits}`"
                )
        for key, rule in body.items():
            if key in _PROFILE_META_KEYS:
                continue
            if key not in INBOX_TYPES:
                raise ProfileCatalogError(
                    f"profile `{name}` declares unknown inbox type "
                    f"`{key}` (allowed: {', '.join(INBOX_TYPES)})"
                )
            if not isinstance(rule, dict):
                raise ProfileCatalogError(
                    f"profile `{name}`.{key} must be a mapping"
                )
            enabled = rule.get("enabled")
            if not isinstance(enabled, bool):
                raise ProfileCatalogError(
                    f"profile `{name}`.{key}.enabled must be a boolean"
                )
            handle = rule.get("handle")
            if enabled and not (isinstance(handle, str) and handle):
                raise ProfileCatalogError(
                    f"profile `{name}`.{key} is enabled but has no "
                    f"`handle` (handles are required for enabled rules)"
                )
            when = rule.get("when")
            if when is not None and not isinstance(when, list):
                raise ProfileCatalogError(
                    f"profile `{name}`.{key}.when must be a list"
                )

    return profiles


def load_profile_catalog(
    path: pathlib.Path | None = None,
) -> dict[str, dict]:
    """Load and validate the YAML catalog. Caches the result per-path.

    ``path`` defaults to ``profile_catalog.yaml`` next to this module.
    Raises :class:`ProfileCatalogError` on:

    - missing top-level ``inbox_profiles`` key
    - profile referencing unknown ``inherits``
    - emit type outside :data:`INBOX_TYPES`
    - rule with ``enabled=true`` but no handle
    """
    target = (path or _DEFAULT_CATALOG_PATH).resolve()
    cached = _CATALOG_CACHE.get(target)
    if cached is not None:
        return cached
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileCatalogError(
            f"profile catalog not found at {target}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ProfileCatalogError(
            f"profile catalog at {target} is not valid YAML: {exc}"
        ) from exc
    profiles = _validate_catalog(raw)
    _CATALOG_CACHE[target] = profiles
    return profiles


def _reset_cache() -> None:
    """Drop the module-level catalog cache (test helper)."""
    _CATALOG_CACHE.clear()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_inheritance(
    name: str,
    catalog: dict[str, dict],
    seen: tuple[str, ...] = (),
) -> dict[str, dict]:
    """Walk ``inherits:`` chain depth-first and shallow-merge per type.

    Returns a freshly-built dict of ``{inbox_type: rule_dict}`` with
    the leaf profile's keys winning over its ancestors'. Cycles raise
    :class:`ProfileCatalogError` rather than recursing forever.
    """
    if name in seen:
        chain = " -> ".join([*seen, name])
        raise ProfileCatalogError(
            f"profile inheritance cycle detected: {chain}"
        )
    if name not in catalog:
        raise ProfileCatalogError(f"unknown inbox profile `{name}`")

    body = catalog[name]
    parent_name = body.get("inherits")
    if isinstance(parent_name, str) and parent_name:
        merged = _resolve_inheritance(
            parent_name, catalog, seen=(*seen, name)
        )
    else:
        merged = {}

    for key, rule in body.items():
        if key in _PROFILE_META_KEYS:
            continue
        # Replace the whole per-type rule. Per-type shallow merge
        # (override of just `handle` etc.) is the pattern-overrides
        # contract, NOT the inheritance contract.
        merged[key] = dict(rule)
    return merged


def _emit_rule_from_raw(inbox_type: str, raw: dict) -> EmitRule:
    """Materialise a validated raw rule dict into an :class:`EmitRule`."""
    enabled = bool(raw.get("enabled", False))
    handle = raw.get("handle") if enabled else None
    when_raw = raw.get("when") or ()
    if isinstance(when_raw, list):
        when = tuple(str(item) for item in when_raw)
    else:
        when = ()
    if not enabled:
        # Disabled rules carry no actionable handle/when — normalise so
        # downstream code can treat the absence consistently.
        return EmitRule(
            type=inbox_type, enabled=False, handle=None, when=()
        )
    return EmitRule(
        type=inbox_type,
        enabled=True,
        handle=str(handle) if handle is not None else None,
        when=when,
    )


def _merge_emit_rule(base: dict, override: dict) -> dict:
    """Per-type shallow merge: override keys win, others inherited.

    ``base`` is the catalog rule, ``override`` the pattern-level
    override. The result is still a raw dict (validated downstream by
    :func:`_emit_rule_from_raw`); we keep it dict-shaped here so the
    same validation path catches operator mistakes in pattern
    overrides too (e.g. ``enabled: true`` with no handle).
    """
    merged = dict(base)
    for key in ("enabled", "handle", "when"):
        if key in override:
            merged[key] = override[key]
    return merged


def resolve_profile(
    profile_name: str,
    overrides: dict[str, dict] | None = None,
    *,
    catalog: dict[str, dict] | None = None,
) -> ResolvedProfile:
    """Resolve ``profile_name`` into a :class:`ResolvedProfile`.

    Override merge rule:

    - For each inbox type in :data:`INBOX_TYPES`: take the profile's
      rule as base, then shallow-merge override keys (``enabled``,
      ``handle``, ``when``) over it. Any type not in ``overrides``
      keeps the profile's value verbatim.
    - For inheritance: ``inherits`` chains are resolved depth-first
      before override merge. Cycles raise
      :class:`ProfileCatalogError`.

    Returns a :class:`ResolvedProfile` with all five inbox types
    populated (disabled rules surface as
    ``EmitRule(enabled=False, handle=None, when=())``).
    """
    cat = catalog if catalog is not None else load_profile_catalog()
    base_rules = _resolve_inheritance(profile_name, cat)

    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ProfileCatalogError(
            f"overrides for profile `{profile_name}` must be a mapping"
        )

    rules: dict[str, EmitRule] = {}
    for inbox_type in INBOX_TYPES:
        base_raw = base_rules.get(inbox_type) or {"enabled": False}
        override_raw = overrides.get(inbox_type)
        if override_raw is not None:
            if not isinstance(override_raw, dict):
                raise ProfileCatalogError(
                    f"override for `{inbox_type}` in profile "
                    f"`{profile_name}` must be a mapping"
                )
            merged = _merge_emit_rule(base_raw, override_raw)
        else:
            merged = base_raw
        if merged.get("enabled") and not merged.get("handle"):
            raise ProfileCatalogError(
                f"resolved rule `{profile_name}`.{inbox_type} is "
                f"enabled but has no `handle` after override merge"
            )
        rules[inbox_type] = _emit_rule_from_raw(inbox_type, merged)

    return ResolvedProfile(profile_name=profile_name, rules=rules)


def resolve_for_pattern(
    pattern_meta: dict,
    *,
    catalog: dict[str, dict] | None = None,
) -> ResolvedProfile:
    """Convenience: read ``spec.inbox.profile`` + overrides from
    pattern frontmatter and call :func:`resolve_profile`.

    If ``spec.inbox`` is missing or ``profile`` is ``None``, returns
    the ``silent`` profile (defensive default — patterns without
    inbox config never emit). Logs a warning so the catalog migration
    can catch missed patterns.
    """
    spec = pattern_meta.get("spec") if isinstance(pattern_meta, dict) else None
    inbox_cfg = spec.get("inbox") if isinstance(spec, dict) else None
    if not isinstance(inbox_cfg, dict):
        pattern_id = (
            pattern_meta.get("id") if isinstance(pattern_meta, dict) else None
        )
        logger.warning(
            "pattern %r has no `spec.inbox` config; falling back to silent",
            pattern_id,
        )
        return resolve_profile(_SILENT_PROFILE_NAME, catalog=catalog)

    profile_name = inbox_cfg.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        pattern_id = pattern_meta.get("id")
        logger.warning(
            "pattern %r has `spec.inbox` without a `profile`; "
            "falling back to silent",
            pattern_id,
        )
        return resolve_profile(_SILENT_PROFILE_NAME, catalog=catalog)

    overrides = inbox_cfg.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ProfileCatalogError(
            f"pattern `spec.inbox.overrides` must be a mapping "
            f"(got {type(overrides).__name__})"
        )
    return resolve_profile(profile_name, overrides, catalog=catalog)


__all__ = [
    "INBOX_TYPES",
    "EmitRule",
    "ResolvedProfile",
    "ProfileCatalogError",
    "load_profile_catalog",
    "resolve_profile",
    "resolve_for_pattern",
]
