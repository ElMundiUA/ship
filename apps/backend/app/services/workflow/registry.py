"""Workflow spec registry (W8.4/W8.5 support).

Resolution order for a named workflow:

1. **Packaged dogfood specs** — YAML files shipped inside the backend
   under ``workflow/specs/`` (pr-review, codebase-audit). These are
   the internal/dogfood launch set (thesis 8 scope decision).
2. (future) **Repo specs** — ``.ship/workflows/<name>.yaml`` in the
   customer repo, fetched via the code-host adapter; the in-repo
   copies under ``.ship/workflows/`` document the contract today and
   the CLI lints them via ``loadSpec.mjs``.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.services.workflow.spec import WorkflowSpec, load_spec

_SPECS_DIR = Path(__file__).parent / "specs"


def list_available_specs() -> list[str]:
    if not _SPECS_DIR.is_dir():
        return []
    return sorted(p.stem for p in _SPECS_DIR.glob("*.yaml"))


def resolve_spec(name: str) -> WorkflowSpec | None:
    """Load a packaged spec by name; ``None`` when unknown."""
    safe = name.strip()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    path = _SPECS_DIR / f"{safe}.yaml"
    if not path.is_file():
        return None
    return load_spec(path.read_text())
