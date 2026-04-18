#!/usr/bin/env python3
"""Lint script: verify every artifact's recorded `content_sha256` is canonical.

Walks `artifacts/<kind>/<id>/ARTIFACT.md` and compares the recorded
`content_sha256` (from the YAML frontmatter) to a freshly computed SHA-256
using the RFC-0005 normalization rule:

    sha256( file_bytes_with_sha_line_value_cleared )

If any artifact has drifted, prints red `FAIL:` lines and exits non-zero so
CI can block the PR.

Usage:
    python3 scripts/ship_artifact_check.py
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

SHA_LINE_RE = re.compile(
    r"^(content_sha256:\s*)([0-9a-fA-F]+)\s*$",
    re.MULTILINE,
)


def canonical_sha(text: str) -> str:
    cleared = SHA_LINE_RE.sub(r"\1", text, count=1)
    return hashlib.sha256(cleared.encode("utf-8")).hexdigest()


def main() -> int:
    if not ARTIFACTS_ROOT.is_dir():
        print(f"{RED}FAIL{RESET} artifacts/ directory not found at {ARTIFACTS_ROOT}")
        return 1

    failures: list[str] = []
    checked = 0
    for md in sorted(ARTIFACTS_ROOT.rglob("ARTIFACT.md")):
        rel = md.relative_to(REPO_ROOT)
        text = md.read_text(encoding="utf-8")
        match = SHA_LINE_RE.search(text)
        if match is None:
            failures.append(f"{rel}: missing content_sha256 frontmatter line")
            continue
        recorded = match.group(2).lower()
        actual = canonical_sha(text)
        if actual != recorded:
            id_match = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
            artifact_id = id_match.group(1) if id_match else rel.parts[-2]
            failures.append(
                f"{rel} (id={artifact_id}): expected {recorded[:12]}…, computed {actual[:12]}…"
            )
        checked += 1

    if failures:
        for line in failures:
            print(f"{RED}FAIL:{RESET} {line}")
        print(
            f"\n{RED}{len(failures)} of {checked} artifacts have drifted{RESET}. "
            "Run `python scripts/restamp_artifact_shas.py` to fix.",
        )
        return 1

    print(f"{GREEN}OK:{RESET} {checked} artifacts canonical (content_sha256 matches).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
