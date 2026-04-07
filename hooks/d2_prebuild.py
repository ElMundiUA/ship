"""Regenerate SVG diagrams from .d2 sources before MkDocs build (optional)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def on_pre_build(config) -> None:  # noqa: ANN001
    root = Path(config.config_file_path or ".").resolve().parent
    diagrams = root / "docs" / "diagrams"
    if not diagrams.is_dir():
        return
    d2 = shutil.which("d2")
    if not d2:
        return
    for src in sorted(diagrams.glob("*.d2")):
        out = src.with_suffix(".svg")
        subprocess.run([d2, str(src), str(out)], check=False)
