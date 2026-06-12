"""Adapter discipline lint (thesis 5, ELS-245).

Fails the build if any adapter under packages/cli/lib/agents/ writes
or reads a TOOL-NATIVE config artifact (.cursorrules, per-tool
CLAUDE.md, codex config, *.mcp.json) — the rejected Variant A. Scoped
to fs-call lines, not bare string literals, so docblocks explaining
the rule don't trip it.

Companion doc: documentation/internal/architecture/agent-adapter-contract.md
"""

from __future__ import annotations

import re
from pathlib import Path

AGENTS_DIR = (
    Path(__file__).resolve().parents[3] / "packages" / "cli" / "lib" / "agents"
)

_TOOL_NATIVE_ARTIFACTS = (
    ".cursorrules",
    "CLAUDE.md",
    ".claude/",
    "codex.toml",
    ".codex/",
    "mcp.json",
)

# fs-access call shapes in the adapters' JS — reading or writing files.
_FS_CALL_RE = re.compile(
    r"(readFileSync|writeFileSync|appendFileSync|createWriteStream|"
    r"openSync|readFile|writeFile|appendFile|\bfs\.\w+)\s*\("
)


def test_agents_dir_exists_and_has_all_adapters() -> None:
    names = {p.name for p in AGENTS_DIR.glob("*.mjs")}
    assert {"index.mjs", "cursor.mjs", "codex.mjs", "claude.mjs", "ship.mjs"} <= names


def test_no_adapter_touches_tool_native_config() -> None:
    for path in sorted(AGENTS_DIR.glob("*.mjs")):
        src = path.read_text()
        for lineno, line in enumerate(src.splitlines(), start=1):
            if not _FS_CALL_RE.search(line):
                continue
            for artifact in _TOOL_NATIVE_ARTIFACTS:
                assert artifact not in line, (
                    f"{path.name}:{lineno} touches tool-native config "
                    f"{artifact!r} via an fs call — Variant A is rejected "
                    "(thesis 5, agent-adapter-contract.md). Inject value "
                    "through the prompt instead."
                )


def test_contract_docblock_present_in_index() -> None:
    src = (AGENTS_DIR / "index.mjs").read_text()
    assert "ADAPTER CONTRACT" in src
    assert "Variant A" in src
    assert "MCP tool" in src  # the rejected inversion
