"""Agent-rule files — Markdown rule blocks the wizard seeds into client repos.

Phase 2.5 retired the on-disk catalog (``artifacts/collections/agent-rules-*/``)
and the CLI's ``shipctl init --copy-rules`` flow. The wizard seed bundle now
embeds the rule files directly, so the customer repo lands ``CLAUDE.md`` /
``AGENTS.md`` / ``.cursor/rules/ship.mdc`` in the same PR as ``.ship/config.yml``.

Each entry binds a wizard-friendly agent slug to:

* a Markdown body shipped under ``backend/app/resources/agent_rule_files/<slug>.md``
* an install path inside the customer repo (``CLAUDE.md`` etc.)
* a label the wizard shows in the agent picker.

The rendered file in the customer repo wraps the body in a marker-delimited
block so a future re-seed (or an operator hand-edit outside the markers)
can refresh just the Ship-owned chunk without clobbering the rest of the
file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final


SHIP_RESOURCES_ROOT: Path = (
    Path(__file__).resolve().parents[1] / "resources" / "agent_rule_files"
)

# Marker pair used to fence the Ship-owned block inside the rule file.
# Mirrors the ``shipctl init --copy-rules`` marker so existing files
# from the legacy flow get refreshed in place rather than duplicated.
_MARKER: Final[str] = "<!-- ship-cli: artifacts-protocol v1 -->"
_END_MARKER: Final[str] = "<!-- /ship-cli: artifacts-protocol v1 -->"


@dataclass(frozen=True, slots=True)
class AgentRuleFile:
    """One installable agent rule file."""

    slug: str         # wizard-facing id: cursor / claude-md / codex / …
    label: str        # human label for the picker
    install_path: str # repo-relative target (CLAUDE.md, .cursor/rules/ship.mdc)


# Wizard-supported agents. Slug → install path + label. The slug is the
# stable contract: ``stack.agents`` in ``.ship/config.yml`` lists these
# strings and the wizard preview shows the labels.
SUPPORTED_AGENTS: Final[dict[str, AgentRuleFile]] = {
    "cursor": AgentRuleFile(
        slug="cursor",
        label="Cursor",
        install_path=".cursor/rules/ship-artifacts-protocol.mdc",
    ),
    "agents-md": AgentRuleFile(
        slug="agents-md",
        label="AGENTS.md (Codex / generic)",
        install_path="AGENTS.md",
    ),
    "claude-md": AgentRuleFile(
        slug="claude-md",
        label="Claude Code (CLAUDE.md)",
        install_path="CLAUDE.md",
    ),
    "claude": AgentRuleFile(
        slug="claude",
        label="Claude Code (CLAUDE.md, alt)",
        install_path="CLAUDE.md",
    ),
    "codex": AgentRuleFile(
        slug="codex",
        label="Codex",
        install_path=".codex/SHIP_API.md",
    ),
    "copilot": AgentRuleFile(
        slug="copilot",
        label="GitHub Copilot",
        install_path=".github/copilot-instructions.md",
    ),
    "aider": AgentRuleFile(
        slug="aider",
        label="Aider",
        install_path="AIDER.md",
    ),
    "cline": AgentRuleFile(
        slug="cline",
        label="Cline / Roo",
        install_path=".clinerules",
    ),
    "continue": AgentRuleFile(
        slug="continue",
        label="Continue.dev",
        install_path=".continue/ship.md",
    ),
    "windsurf": AgentRuleFile(
        slug="windsurf",
        label="Windsurf",
        install_path=".windsurfrules",
    ),
    "zed": AgentRuleFile(
        slug="zed",
        label="Zed",
        install_path=".zed/ship.md",
    ),
    "gemini": AgentRuleFile(
        slug="gemini",
        label="Gemini CLI",
        install_path="GEMINI.md",
    ),
    "opencode": AgentRuleFile(
        slug="opencode",
        label="OpenCode",
        install_path=".opencode/ship.md",
    ),
    "cursor-cloud": AgentRuleFile(
        slug="cursor-cloud",
        label="Cursor Cloud Agent",
        install_path=".cursor/environments.json",
    ),
}


class AgentRuleFileError(RuntimeError):
    """Raised on a malformed/missing agent rule file resource."""


@lru_cache(maxsize=1)
def _load_bodies() -> dict[str, str]:
    """Read every ``<slug>.md`` under the resources dir into memory once."""
    out: dict[str, str] = {}
    if not SHIP_RESOURCES_ROOT.is_dir():
        return out
    for path in sorted(SHIP_RESOURCES_ROOT.glob("*.md")):
        out[path.stem] = path.read_text(encoding="utf-8")
    return out


def reset_body_cache() -> None:
    """Test helper — drop the on-disk body cache."""
    _load_bodies.cache_clear()


def list_supported_agents() -> list[AgentRuleFile]:
    """Wizard picker source — every agent slug we ship a rule file for."""
    return [SUPPORTED_AGENTS[slug] for slug in sorted(SUPPORTED_AGENTS)]


def is_supported(slug: str) -> bool:
    return slug in SUPPORTED_AGENTS


def render_rule_file(slug: str) -> tuple[str, str]:
    """Return ``(install_path, file_body)`` for one agent slug.

    Wraps the resource body in a marker-fenced block so a re-seed can
    refresh just the Ship-owned chunk in place. Raises
    :class:`AgentRuleFileError` for unknown slugs or missing resources.
    """
    entry = SUPPORTED_AGENTS.get(slug)
    if entry is None:
        raise AgentRuleFileError(
            f"unknown agent rule slug: {slug!r}; "
            f"expected one of {sorted(SUPPORTED_AGENTS)}"
        )
    bodies = _load_bodies()
    body = bodies.get(slug)
    if body is None:
        raise AgentRuleFileError(
            f"agent rule body missing on disk for {slug!r} "
            f"(expected {SHIP_RESOURCES_ROOT / (slug + '.md')})"
        )
    rendered = (
        f"{_MARKER}\n\n{body.rstrip()}\n\n{_END_MARKER}\n"
    )
    return entry.install_path, rendered


def render_rule_files(slugs: list[str] | tuple[str, ...]) -> list[tuple[str, str]]:
    """Render multiple agents — drops unknown slugs with a logged warning.

    Used by the wizard seed flow. Two slugs that share an install_path
    (``claude`` + ``claude-md``) emit two entries but the seed bundle
    deduplicates on path so only the first body wins; that's deliberate —
    overlapping picks should be filtered earlier in the wizard UI.
    """
    log = logging.getLogger("ship.agent_rule_files")
    out: list[tuple[str, str]] = []
    for slug in slugs:
        if not is_supported(slug):
            log.warning("skipping unknown agent slug %r in seed bundle", slug)
            continue
        out.append(render_rule_file(slug))
    return out


__all__ = [
    "AgentRuleFile",
    "AgentRuleFileError",
    "SHIP_RESOURCES_ROOT",
    "SUPPORTED_AGENTS",
    "list_supported_agents",
    "is_supported",
    "render_rule_file",
    "render_rule_files",
    "reset_body_cache",
]
