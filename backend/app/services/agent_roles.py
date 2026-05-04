"""Agent role registry — Ship defaults + workspace overrides/clones.

Two-tier model (Phase 2.4):

* **Ship defaults** live as files under
  ``backend/app/resources/agent_roles/<slug>.md`` with a tiny
  frontmatter (``name``, optional ``fsm_stage``) and a markdown body.
  Read-only at runtime; updated by Ship releases.
* **Workspace rows** live in the ``agent_roles`` table (one row per
  ``(workspace_id, slug)``). Two flavours:
    - **Override** — ``slug`` matches a Ship default; the row's prompt
      shadows the file body for the owning workspace.
    - **Clone** — ``slug`` is a new identifier within the workspace;
      ``base_role_slug`` records which file was the seed (so the UI
      can render "Cloned from developer").

The runtime resolver (used by ``shipctl run``) prefers the workspace
row; on miss it returns the Ship default. The CRUD routes layer
on top of this module.

Directory layout
----------------

::

    backend/app/resources/agent_roles/
        system.md              # the shared base prompt
        developer.md
        ba.md
        intake.md              # has fsm_stage: task_intake
        ...

Frontmatter shape (everything but ``name`` is optional):

::

    ---
    name: Developer
    fsm_stage: dev_implementation
    ---
    <markdown body>
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


SHIP_RESOURCES_ROOT: Path = Path(__file__).resolve().parents[1] / "resources" / "agent_roles"

# Slug of the shared base prompt (was ``common-base`` in the old
# catalog; ``system`` in the new vocabulary). Hot-path callers that
# render the agent prompt prepend this body before the specialist
# body so workspace policies, exit protocol, and tone live in one
# place.
SYSTEM_SLUG: str = "system"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class AgentRoleError(RuntimeError):
    """Raised on malformed Ship default files or invalid input."""


@dataclass(frozen=True, slots=True)
class AgentRoleDefault:
    """One Ship-shipped default specialist (file-backed, read-only)."""

    slug: str
    name: str
    prompt: str
    fsm_stage: str | None
    # Phase 4 — hard-deny list the runner honours. Empty tuple for
    # roles that don't declare any restrictions (the common case).
    # The reviewer slug ships ``("git_commit", "git_push",
    # "git_amend")`` so the runner refuses to invoke commit / push
    # tools no matter how persuasive the prompt drift gets.
    denied_tools: tuple[str, ...] = ()


def is_valid_slug(slug: str) -> bool:
    """Slug grammar — kebab-case, 1–64 chars, [a-z0-9-]."""
    return bool(_SLUG_RE.match(slug or ""))


def _split_frontmatter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise AgentRoleError(
            f"{source.name}: missing YAML frontmatter (expected leading '---')."
        )
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise AgentRoleError(f"{source.name}: unterminated frontmatter.")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise AgentRoleError(f"{source.name}: invalid frontmatter YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise AgentRoleError(f"{source.name}: frontmatter must be a mapping.")
    body = text[m.end():]
    return meta, body


@lru_cache(maxsize=1)
def _load_defaults() -> dict[str, AgentRoleDefault]:
    """Walk the resources dir once and cache the parsed defaults.

    Cache invalidation is a Ship-restart concern — the resources dir
    is shipped with the image and never edited at runtime.
    """
    if not SHIP_RESOURCES_ROOT.is_dir():
        return {}
    out: dict[str, AgentRoleDefault] = {}
    for path in sorted(SHIP_RESOURCES_ROOT.glob("*.md")):
        slug = path.stem
        if not is_valid_slug(slug):
            raise AgentRoleError(
                f"{path.name}: filename '{slug}' is not a valid slug "
                "(lowercase kebab-case, 1–64 chars)."
            )
        text = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text, path)
        name = meta.get("name") or slug
        if not isinstance(name, str):
            raise AgentRoleError(f"{path.name}: 'name' must be a string.")
        fsm_raw = meta.get("fsm_stage")
        fsm_stage: str | None
        if fsm_raw is None:
            fsm_stage = None
        elif isinstance(fsm_raw, str) and fsm_raw.strip():
            fsm_stage = fsm_raw.strip()
        else:
            raise AgentRoleError(
                f"{path.name}: 'fsm_stage' must be a non-empty string or omitted."
            )
        denied_raw = meta.get("denied_tools")
        denied_tools: tuple[str, ...]
        if denied_raw is None:
            denied_tools = ()
        elif isinstance(denied_raw, list) and all(
            isinstance(item, str) and item.strip() for item in denied_raw
        ):
            denied_tools = tuple(item.strip() for item in denied_raw)
        else:
            raise AgentRoleError(
                f"{path.name}: 'denied_tools' must be a list of non-empty "
                "strings or omitted."
            )
        out[slug] = AgentRoleDefault(
            slug=slug,
            name=name,
            prompt=body,
            fsm_stage=fsm_stage,
            denied_tools=denied_tools,
        )
    return out


def list_defaults() -> list[AgentRoleDefault]:
    """All Ship-shipped defaults, sorted by slug."""
    return list(_load_defaults().values())


def get_default(slug: str) -> AgentRoleDefault | None:
    """One Ship default by slug, or ``None`` when missing."""
    return _load_defaults().get(slug)


def reset_default_cache() -> None:
    """Test helper — reload the on-disk defaults."""
    _load_defaults.cache_clear()


__all__ = [
    "AgentRoleDefault",
    "AgentRoleError",
    "SHIP_RESOURCES_ROOT",
    "SYSTEM_SLUG",
    "is_valid_slug",
    "list_defaults",
    "get_default",
    "reset_default_cache",
]
