#!/usr/bin/env python3
"""RFC-0008 Phase 1 follow-up — rewrite old pattern ids in references.

Mechanically replaces the 21 old pattern ids with their new
``<category>-<name>`` form across code, docs, tests, and collection
manifests.

**Does not touch:**

- ``documentation/protocol/rfc-000[1-7]-*.md`` — those RFCs describe
  historical / pre-RFC-0008 state. Rewriting their quoted ids would
  lie about what the protocol was at the time.
- ``scripts/rfc_0008_rename.py`` / ``scripts/rfc_0008_refs.py`` — the
  migration scripts themselves document the mapping.
- ``artifacts/patterns/`` — handled by ``rfc_0008_rename.py``.

Run after ``rfc_0008_rename.py``. Idempotent: a second run is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# Ordered longest-first so ``cloud-base`` doesn't accidentally match
# inside ``cloud-base-guardrails`` or similar. With this set the
# cross-token overlap isn't real today, but pinning the order keeps the
# migration safe if we add longer ids later.
RENAMES: list[tuple[str, str]] = sorted(
    [
        ("cloud-workflow-self-heal", "op-workflow-self-heal"),
        ("cloud-security-officer", "role-security-officer"),
        ("cloud-tech-architect", "role-tech-architect"),
        ("cloud-qa-architect", "role-qa-architect"),
        ("cloud-clarification", "role-clarification"),
        ("cloud-developer", "role-developer"),
        ("cloud-intake", "role-intake"),
        ("cloud-base", "common-base"),
        ("cloud-ba", "role-ba"),
        ("catalog-a13-daily-retro", "flow-daily-retro"),
        ("catalog-a12-learning", "flow-learning-capture"),
        ("catalog-a11-retry-sweep", "op-retry-sweep"),
        ("catalog-a10-human-handoff", "flow-human-handoff"),
        ("catalog-a9-qa", "flow-qa-acceptance"),
        ("catalog-a8-preview-failure-recovery", "flow-preview-failure-recovery"),
        ("catalog-a7-preview-validation", "flow-preview-validation"),
        ("catalog-a6-check-failure-recovery", "flow-check-failure-recovery"),
        ("catalog-a5-pr-self-review", "flow-pr-self-review"),
        ("adopt-ship-generic", "onboard-adopt"),
        ("seed-knowledge-starters", "onboard-seed-knowledge"),
    ],
    key=lambda pair: len(pair[0]),
    reverse=True,
)


# "kickoff" → "common-kickoff" is intentionally handled separately
# because the token also names the ``shipctl kickoff`` *command* and
# frontmatter ``group: kickoff`` etc. — we don't want to rewrite those.
# The only real references to the pattern id ``kickoff`` are:
#   - ``patternId: "kickoff"`` in ``cli/lib/commands/kickoff.mjs``
#   - YAML pins / markdown examples that literally quote the id string
#     in a pattern-ish context (``"kickoff"``, ``pattern:kickoff``,
#     ``/patterns/kickoff``).
# Everything else (help text, command name, doc prose) stays put.
KICKOFF_MARKERS: tuple[tuple[str, str], ...] = (
    ('patternId: "kickoff"', 'patternId: "common-kickoff"'),
    ('pattern:kickoff@', 'pattern:common-kickoff@'),
    ('/patterns/kickoff', '/patterns/common-kickoff'),
    ('"pattern":"kickoff"', '"pattern":"common-kickoff"'),
    ('"id":"kickoff"', '"id":"common-kickoff"'),
    ('id: kickoff\n', 'id: common-kickoff\n'),  # rare YAML stanza
    ('(kickoff)', '(common-kickoff)'),
    ("shipctl pattern fetch kickoff", "shipctl pattern fetch common-kickoff"),
    ("shipctl pattern show kickoff", "shipctl pattern show common-kickoff"),
)


EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    "artifacts/patterns",  # handled upstream by rfc_0008_rename.py
}

EXCLUDE_FILES = {
    "scripts/rfc_0008_rename.py",
    "scripts/rfc_0008_refs.py",
    "documentation/protocol/rfc-0001-artifacts-protocol.md",
    "documentation/protocol/rfc-0002-shipctl-config.md",
    "documentation/protocol/rfc-0003-telemetry-and-feedback.md",
    "documentation/protocol/rfc-0004-adapters.md",
    "documentation/protocol/rfc-0005-artifact-folder-spec-v2.md",
    "documentation/protocol/rfc-0006-cloud-platform-foundations.md",
    "documentation/protocol/rfc-0007-lanes-and-run-agent.md",
    # RFC-0008 itself documents the rename table.
    "documentation/protocol/rfc-0008-catalog-reform.md",
}


TEXT_SUFFIXES = {
    ".md", ".mjs", ".js", ".ts", ".tsx", ".py", ".yml", ".yaml",
    ".json", ".toml", ".sh",
}


def _skip(path: Path) -> bool:
    rel = path.relative_to(REPO).as_posix()
    for prefix in EXCLUDE_DIRS:
        if rel.startswith(prefix + "/") or rel == prefix:
            return True
    if rel in EXCLUDE_FILES:
        return True
    if path.suffix not in TEXT_SUFFIXES:
        return True
    return False


def _rewrite(text: str) -> tuple[str, int]:
    total = 0
    for old, new in RENAMES:
        count = text.count(old)
        if count == 0:
            continue
        text = text.replace(old, new)
        total += count
    for old, new in KICKOFF_MARKERS:
        count = text.count(old)
        if count == 0:
            continue
        text = text.replace(old, new)
        total += count
    return text, total


def main() -> int:
    touched = 0
    reps = 0
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        if _skip(path):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, count = _rewrite(raw)
        if count == 0 or new == raw:
            continue
        path.write_text(new, encoding="utf-8")
        rel = path.relative_to(REPO)
        print(f"updated: {rel} ({count} refs)")
        touched += 1
        reps += count
    print(f"\nrewrote {reps} references across {touched} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
