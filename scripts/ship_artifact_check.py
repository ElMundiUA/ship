#!/usr/bin/env python3
"""Lint script: verify manifest `content_sha256` matches the referenced file.

Walks every entry in patterns/tools/workflows/collections manifests and
compares the recorded `content_sha256` to a freshly computed SHA-256 of the
referenced file. If any entry has drifted, prints red `FAIL:` lines and exits
non-zero so CI can block the PR.

Usage:
    python3 scripts/ship_artifact_check.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

MANIFESTS = (
    ("pattern", REPO_ROOT / "patterns" / "manifest.json", "patterns"),
    ("tool", REPO_ROOT / "tools" / "manifest.json", "tools"),
    ("workflow", REPO_ROOT / "workflows" / "manifest.json", "workflows"),
    ("collection", REPO_ROOT / "collections" / "manifest.json", "collections"),
)

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifest(kind: str, manifest_path: Path, array_key: str) -> tuple[int, list[str]]:
    if not manifest_path.is_file():
        return 0, []
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = data.get(array_key) or []
    failures: list[str] = []
    checked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        if not isinstance(rel, str):
            continue
        target = (REPO_ROOT / rel).resolve()
        try:
            target.relative_to(REPO_ROOT.resolve())
        except ValueError:
            continue
        if not target.is_file() or target.suffix.lower() not in {".md", ".txt"}:
            continue
        stored = entry.get("content_sha256")
        actual = sha256_of(target)
        version = entry.get("version", "?.?.?")
        ident = entry.get("id", "?")
        checked += 1
        if stored != actual:
            failures.append(
                f"{RED}FAIL:{RESET} {kind}:{ident} content changed but manifest "
                f"version still {version} (expected bump). "
                f"stored_sha={stored}, actual_sha={actual}"
            )
    return checked, failures


def main() -> int:
    total_checked = 0
    all_failures: list[str] = []
    for kind, path, array_key in MANIFESTS:
        count, failures = check_manifest(kind, path, array_key)
        total_checked += count
        all_failures.extend(failures)

    if all_failures:
        for line in all_failures:
            print(line)
        print(f"{RED}{len(all_failures)} drift(s) detected across {total_checked} checked entries.{RESET}")
        return 1

    print(f"{GREEN}OK:{RESET} {total_checked} manifest entries checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
