"""Loader for the SDLC bootstrap blueprints (BS1).

Each ``app/resources/sdlc_blueprints/<project_type>.md`` file pairs a
machine-readable YAML frontmatter (what capabilities a project of this
type *should* have, how it ships, which secrets it needs) with a
human-readable body (what the operator sets up outside Ship + an
execution checklist). The readiness assessor (:mod:`sdlc_readiness`)
consumes the frontmatter; the recommendation surface (BS2) and the
bootstrap epic generator (BS3) consume the body + checklist.

Mirrors the resource-loading pattern in :mod:`agent_roles` — parse once,
cache for the process lifetime (the files ship with the image and are
never edited at runtime).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


SHIP_BLUEPRINTS_ROOT: Path = (
    Path(__file__).resolve().parents[1] / "resources" / "sdlc_blueprints"
)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
# A markdown task-list line: ``- [ ] text`` or ``- [x] text``.
_CHECKLIST_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(?P<text>.+?)\s*$")
# An operator-owned checklist item is tagged ``(you)`` (often bold).
_YOU_MARKER_RE = re.compile(r"\(you\)", re.IGNORECASE)


class BlueprintError(RuntimeError):
    """Raised on a malformed blueprint file."""


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    """One execution-checklist line from a blueprint body.

    ``actor`` is ``"user"`` for items the operator must verify (tagged
    ``(you)`` in the source) and ``"ship"`` for items the devops agent
    ticks as the bootstrap epic lands.
    """

    text: str
    actor: str


@dataclass(frozen=True, slots=True)
class Blueprint:
    """One parsed SDLC bootstrap blueprint."""

    project_type: str
    # Frontmatter schema version. Lets BS2/BS3 record which blueprint
    # shape produced a recommendation so a later schema change can be
    # reconciled. Defaults to 1 when the file omits it.
    version: int
    display_name: str
    delivery: str
    environments: tuple[str, ...]
    # capability name → ordered probe patterns (file globs / dep tokens)
    # that, if any match the repo, satisfy the capability.
    capabilities: dict[str, tuple[str, ...]]
    # subset of ``capabilities`` that must be satisfied for "ready".
    required: tuple[str, ...]
    secrets_required: tuple[str, ...]
    secrets_optional: tuple[str, ...]
    checklist: tuple[ChecklistItem, ...]
    body: str


def _split_frontmatter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise BlueprintError(
            f"{source.name}: missing YAML frontmatter (expected leading '---')."
        )
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise BlueprintError(f"{source.name}: unterminated frontmatter.")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise BlueprintError(
            f"{source.name}: invalid frontmatter YAML: {exc}"
        ) from exc
    if not isinstance(meta, dict):
        raise BlueprintError(f"{source.name}: frontmatter must be a mapping.")
    return meta, text[m.end() :]


def _str_list(value: Any) -> tuple[str, ...]:
    """Coerce a YAML list to a tuple of stripped non-empty strings."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BlueprintError(f"expected a list, got {type(value).__name__}")
    return tuple(str(v).strip() for v in value if str(v).strip())


def parse_checklist(body: str) -> tuple[ChecklistItem, ...]:
    """Extract task-list items from a blueprint body.

    Strips markdown bold + the ``(you)`` actor marker from the visible
    text while using the marker to set ``actor``.
    """
    items: list[ChecklistItem] = []
    for line in body.splitlines():
        m = _CHECKLIST_RE.match(line)
        if not m:
            continue
        raw = m.group("text")
        actor = "user" if _YOU_MARKER_RE.search(raw) else "ship"
        cleaned = _YOU_MARKER_RE.sub("", raw)
        cleaned = cleaned.replace("**", "").strip(" -*\t")
        if cleaned:
            items.append(ChecklistItem(text=cleaned, actor=actor))
    return tuple(items)


@lru_cache(maxsize=1)
def _load() -> dict[str, Blueprint]:
    out: dict[str, Blueprint] = {}
    if not SHIP_BLUEPRINTS_ROOT.is_dir():
        return out
    for path in sorted(SHIP_BLUEPRINTS_ROOT.glob("*.md")):
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)

        project_type = meta.get("project_type")
        if not isinstance(project_type, str) or not project_type.strip():
            raise BlueprintError(
                f"{path.name}: 'project_type' must be a non-empty string."
            )
        project_type = project_type.strip()

        detect = meta.get("detect") or {}
        caps_raw = detect.get("capabilities") if isinstance(detect, dict) else None
        capabilities: dict[str, tuple[str, ...]] = {}
        if isinstance(caps_raw, dict):
            for cap, probes in caps_raw.items():
                capabilities[str(cap)] = _str_list(probes)

        required = _str_list(meta.get("required"))
        unknown = [c for c in required if c not in capabilities]
        if unknown:
            raise BlueprintError(
                f"{path.name}: required capabilities {unknown} are not declared "
                "under detect.capabilities."
            )

        secrets = meta.get("secrets") or {}
        if not isinstance(secrets, dict):
            raise BlueprintError(f"{path.name}: 'secrets' must be a mapping.")

        try:
            version = int(meta.get("version") or 1)
        except (TypeError, ValueError) as exc:
            raise BlueprintError(
                f"{path.name}: 'version' must be an integer."
            ) from exc

        out[project_type] = Blueprint(
            project_type=project_type,
            version=version,
            display_name=str(meta.get("display_name") or project_type),
            delivery=str(meta.get("delivery") or "unknown"),
            environments=_str_list(meta.get("environments")),
            capabilities=capabilities,
            required=required,
            secrets_required=_str_list(secrets.get("required")),
            secrets_optional=_str_list(secrets.get("optional")),
            checklist=parse_checklist(body),
            body=body,
        )
    return out


def load_blueprint(project_type: str | None) -> Blueprint | None:
    """Return the blueprint for ``project_type`` or ``None`` if none ships."""
    if not project_type:
        return None
    return _load().get(project_type)


def load_all_blueprints() -> dict[str, Blueprint]:
    """Return a copy of the project_type → blueprint map."""
    return dict(_load())


__all__ = [
    "Blueprint",
    "BlueprintError",
    "ChecklistItem",
    "load_all_blueprints",
    "load_blueprint",
    "parse_checklist",
]
