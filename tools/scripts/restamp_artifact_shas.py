#!/usr/bin/env python3
"""Re-stamp `content_sha256` for every artifacts/<kind>/<id>/ARTIFACT.md
using the RFC-0005 canonical hashing rule:

    content_sha256 := sha256( file_bytes_with_sha_line_value_cleared )

Where "value cleared" means the literal `content_sha256: ` line keeps the
key but the hex value is replaced by the empty string. This makes both
server stamping and client verification deterministic and avoids the
chicken-and-egg of hashing a file that contains its own hash.

Idempotent: a second run produces no diff.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts"

SHA_LINE_RE = re.compile(
    r"^(content_sha256:\s*)([0-9a-fA-F]*)\s*$",
    re.MULTILINE,
)


def restamp(path: Path) -> tuple[bool, str]:
    """Return (changed, new_sha) for `path`. Writes only if sha changed."""
    raw = path.read_text(encoding="utf-8")
    match = SHA_LINE_RE.search(raw)
    if match is None:
        return (False, "")
    cleared = SHA_LINE_RE.sub(r"\1", raw, count=1)
    new_sha = hashlib.sha256(cleared.encode("utf-8")).hexdigest()
    new_text = SHA_LINE_RE.sub(rf"\g<1>{new_sha}", raw, count=1)
    if new_text == raw:
        return (False, new_sha)
    path.write_text(new_text, encoding="utf-8")
    return (True, new_sha)


def main() -> int:
    changed = 0
    total = 0
    for md in sorted(ROOT.rglob("ARTIFACT.md")):
        total += 1
        was_changed, sha = restamp(md)
        if was_changed:
            changed += 1
            print(f"stamped: {md.relative_to(REPO)}  →  {sha[:16]}…")
    print(f"\nstamped {changed}/{total} artifacts (others already canonical).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
