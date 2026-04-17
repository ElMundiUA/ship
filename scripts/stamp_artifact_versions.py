#!/usr/bin/env python3
"""Stamp version metadata into every artifact manifest.

Iterates patterns/tools/workflows/collections manifests at the repo root,
and for each entry whose `path` points to an existing .md/.txt file:

- adds missing default fields (version, channel, min_shipctl, deprecated,
  replaced_by, yanked)
- recomputes `content_sha256` from the file bytes
- sets `updated_at` to the last git commit time for that path, falling back
  to the file's mtime when git history is unavailable

Preserves the existing top-level key order and nests the per-entry fields in a
stable, readable layout. Writes each manifest back atomically.

Usage:

    python3 scripts/stamp_artifact_versions.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

MANIFESTS = (
    ("patterns", REPO_ROOT / "patterns" / "manifest.json", "patterns"),
    ("tools", REPO_ROOT / "tools" / "manifest.json", "tools"),
    ("workflows", REPO_ROOT / "workflows" / "manifest.json", "workflows"),
    ("collections", REPO_ROOT / "collections" / "manifest.json", "collections"),
)

DEFAULTS: "OrderedDict[str, object]" = OrderedDict(
    [
        ("version", "1.0.0"),
        ("channel", "stable"),
        ("min_shipctl", "0.3.0"),
        ("deprecated", False),
        ("replaced_by", None),
        ("yanked", False),
    ]
)

PREFERRED_ORDER = [
    "id",
    "title",
    "summary",
    "path",
    "tags",
    "group",
    "version",
    "content_sha256",
    "updated_at",
    "channel",
    "min_shipctl",
    "deprecated",
    "replaced_by",
    "yanked",
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_updated_at(rel_path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", rel_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    stamp = out.stdout.strip()
    return stamp or None


def mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def order_entry(entry: dict) -> "OrderedDict[str, object]":
    ordered: "OrderedDict[str, object]" = OrderedDict()
    for key in PREFERRED_ORDER:
        if key in entry:
            ordered[key] = entry[key]
    for key, value in entry.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def stamp_entry(entry: dict) -> bool:
    """Return True if the entry refers to a stampable file."""
    rel = entry.get("path")
    if not isinstance(rel, str) or not rel.strip():
        return False
    target = (REPO_ROOT / rel).resolve()
    try:
        target.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return False
    if not target.is_file():
        return False
    if target.suffix.lower() not in {".md", ".txt"}:
        return False

    for key, default in DEFAULTS.items():
        entry.setdefault(key, default)

    entry["content_sha256"] = sha256_of(target)
    entry["updated_at"] = git_updated_at(rel) or mtime_iso(target)
    return True


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def process_manifest(manifest_path: Path, array_key: str) -> int:
    if not manifest_path.is_file():
        print(f"skip: {manifest_path} (missing)")
        return 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get(array_key)
    if not isinstance(items, list):
        print(f"skip: {manifest_path} (no `{array_key}` array)")
        return 0

    stamped = 0
    new_items = []
    for entry in items:
        if isinstance(entry, dict) and stamp_entry(entry):
            new_items.append(order_entry(entry))
            stamped += 1
        else:
            new_items.append(entry)
    data[array_key] = new_items

    atomic_write(manifest_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return stamped


def main() -> int:
    total = 0
    for _kind, path, array_key in MANIFESTS:
        total += process_manifest(path, array_key)
    print(f"{total} entries stamped across {len(MANIFESTS)} manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
