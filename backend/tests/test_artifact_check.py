from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ship_artifact_check.py"


def _run_check():
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_artifact_check_passes_on_clean_repo():
    result = _run_check()
    assert result.returncode == 0, result.stdout + result.stderr


def test_artifact_check_detects_drift():
    # Mutate a known small pattern file temporarily and expect FAIL.
    patterns = REPO_ROOT / "patterns" / "manifest.json"
    import json

    data = json.loads(patterns.read_text(encoding="utf-8"))
    entry = data["patterns"][0]
    target = REPO_ROOT / entry["path"]
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n<!-- test drift injection -->\n")
        result = _run_check()
        assert result.returncode == 1, (
            f"expected drift failure; stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "FAIL" in result.stdout
        assert entry["id"] in result.stdout
    finally:
        target.write_bytes(original)

    # Confirm the repo is clean again.
    clean = _run_check()
    assert clean.returncode == 0, clean.stdout + clean.stderr
