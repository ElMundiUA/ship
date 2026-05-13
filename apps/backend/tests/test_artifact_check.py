from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ship_artifact_check.py"
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

SHA_LINE_RE = re.compile(
    r"^(content_sha256:\s*)([0-9a-fA-F]+)\s*$",
    re.MULTILINE,
)


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
    """Mutate one ARTIFACT.md so its body no longer matches its recorded sha,
    then confirm the lint script flags it and points at the right id."""

    target = next(ARTIFACTS_ROOT.rglob("ARTIFACT.md"))
    original = target.read_bytes()
    id_match = re.search(rb"^id:\s*(\S+)", original, re.MULTILINE)
    assert id_match, f"no id: line in {target}"
    artifact_id = id_match.group(1).decode("utf-8")

    try:
        target.write_bytes(original + b"\n<!-- test drift injection -->\n")
        result = _run_check()
        assert result.returncode == 1, (
            f"expected drift failure; stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "FAIL" in result.stdout
        assert artifact_id in result.stdout
    finally:
        target.write_bytes(original)

    clean = _run_check()
    assert clean.returncode == 0, clean.stdout + clean.stderr
