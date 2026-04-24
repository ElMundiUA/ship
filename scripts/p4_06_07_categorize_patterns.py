#!/usr/bin/env python3
"""P4-06 + P4-07 — sweep user-facing pattern frontmatter to add catalog
metadata (``category``, optional ``subcategory`` / ``secondary_categories``,
and ``critical``) so the Plays/Inbox redesign coverage endpoint and
``/plays`` sidebar can group patterns without inferring categories from
prompt text.

Mapping is the canonical one from
``documentation/internal/inbox-redesign-planning.md`` §2 — do NOT improvise
here. If you need to extend, extend §2 first and re-run.

Run from repo root::

    python scripts/p4_06_07_categorize_patterns.py

Idempotent: re-running on a pattern that already has a top-level
``category:`` line is a no-op (the file is left untouched).

After this script edits files, run ``scripts/restamp_artifact_shas.py`` to
refresh ``content_sha256`` for every modified ARTIFACT.md.

YAML strategy
-------------
We do **not** round-trip with ruamel.yaml — every existing ARTIFACT.md uses
a hand-curated frontmatter ordering (``artifact_kind`` → ``id`` → ``name``
→ ``version`` → … → ``description`` → ``spec``) and round-tripping risks
re-ordering keys, mangling the ``description: >-`` folded scalar, and
churning quoting style. Instead we do a targeted text insert: locate the
``spec:`` line at column 0 of the YAML frontmatter and insert the new
top-level keys immediately above it. This preserves exact formatting of
everything else and keeps the diff small (one chunk per file).
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Optional

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts" / "patterns"

# --- Canonical mapping (from planning §2) -------------------------------
# pattern_id -> (category, subcategory_or_None, secondary_categories_or_None)
#
# Multi-category patterns (3): primary is the FIRST category §2 lists for
# them. The other category goes into ``secondary_categories``.
#
#   scan-docs-freshness   : code_review (primary)        + knowledge_docs
#   flow-runbook-freshness: incident_response (primary)  + knowledge_docs
#   flow-human-handoff    : code_review (primary)        + incident_response
#
# (For ``scan-docs-freshness``, §2 lists it under Code review first, then
# Knowledge & Docs — primary = code_review.)
# (For ``flow-runbook-freshness``, §2 lists it under Incident response
# first, then Knowledge & Docs — primary = incident_response.)
# (For ``flow-human-handoff``, §2 lists it under Code review first, then
# Incident response — primary = code_review.)

CATEGORY_MAP: dict[str, tuple[str, Optional[str], Optional[list[str]]]] = {
    # --- Code review (12) ---
    "flow-pr-self-review": ("code_review", None, None),
    "flow-blast-radius": ("code_review", None, None),
    "flow-qa-acceptance": ("code_review", None, None),
    "flow-preview-validation": ("code_review", None, None),
    "flow-preview-failure-recovery": ("code_review", None, None),
    "flow-check-failure-recovery": ("code_review", None, None),
    "flow-human-handoff": ("code_review", None, ["incident_response"]),
    "scan-test-coverage": ("code_review", None, None),
    "scan-api-contract": ("code_review", None, None),
    "scan-tech-debt": ("code_review", None, None),
    "scan-dead-code": ("code_review", None, None),
    "scan-docs-freshness": ("code_review", None, ["knowledge_docs"]),
    # --- Health checks: security (8) ---
    "scan-security-deps": ("health_checks", "security", None),
    "scan-license-deps": ("health_checks", "security", None),
    "scan-pii-leakage": ("health_checks", "security", None),
    "scan-permissions-audit": ("health_checks", "security", None),
    "scan-iam-policy-diff": ("health_checks", "security", None),
    "scan-k8s-policy": ("health_checks", "security", None),
    "scan-signing-notarization": ("health_checks", "security", None),
    "scan-audit-log-integrity": ("health_checks", "security", None),
    # --- Health checks: performance (9) ---
    "scan-performance-budget": ("health_checks", "performance", None),
    "scan-app-size-budget": ("health_checks", "performance", None),
    "scan-firmware-size": ("health_checks", "performance", None),
    "scan-installer-size": ("health_checks", "performance", None),
    "scan-asset-budget": ("health_checks", "performance", None),
    "scan-power-profile": ("health_checks", "performance", None),
    "scan-build-frametime": ("health_checks", "performance", None),
    "scan-mobile-crash-rate": ("health_checks", "performance", None),
    "scan-slo-health": ("health_checks", "performance", None),
    # --- Health checks: compliance (5) ---
    "scan-consent-drift": ("health_checks", "compliance", None),
    "scan-store-metadata": ("health_checks", "compliance", None),
    "scan-localization-gap": ("health_checks", "compliance", None),
    "scan-os-support-matrix": ("health_checks", "compliance", None),
    "scan-hal-abi-lock": ("health_checks", "compliance", None),
    # --- Health checks: cost (3) ---
    "scan-cost-delta": ("health_checks", "cost", None),
    "scan-terraform-drift": ("health_checks", "cost", None),
    "scan-env-var-catalog": ("health_checks", "cost", None),
    # --- Health checks: ml_quality (5) ---
    "scan-data-drift": ("health_checks", "ml_quality", None),
    "scan-bias-fairness": ("health_checks", "ml_quality", None),
    "scan-model-eval": ("health_checks", "ml_quality", None),
    "scan-feature-schema": ("health_checks", "ml_quality", None),
    "scan-training-repro": ("health_checks", "ml_quality", None),
    # --- Health checks: other (3) ---
    "scan-a11y": ("health_checks", "other", None),
    "scan-bom-delta": ("health_checks", "other", None),
    "scan-sbom-drift": ("health_checks", "other", None),
    # --- Release ops (10) ---
    "flow-release-notes": ("release_ops", None, None),
    "flow-store-submission": ("release_ops", None, None),
    "flow-cert-compliance": ("release_ops", None, None),
    "flow-compliance-artifact": ("release_ops", None, None),
    "flow-autoupdate-rollout": ("release_ops", None, None),
    "flow-ota-channel": ("release_ops", None, None),
    "flow-beta-distribution": ("release_ops", None, None),
    "flow-model-card": ("release_ops", None, None),
    "flow-dependency-update": ("release_ops", None, None),
    "flow-live-ops-calendar": ("release_ops", None, None),
    # --- Incident response (5) ---
    "flow-incident-postmortem": ("incident_response", None, None),
    "flow-oncall-handoff": ("incident_response", None, None),
    "flow-runbook-freshness": ("incident_response", None, ["knowledge_docs"]),
    # flow-human-handoff handled above (primary = code_review).
    "role-clarification": ("incident_response", None, None),
    # --- Knowledge & docs (4) ---
    # scan-docs-freshness + flow-runbook-freshness handled above.
    "flow-learning-capture": ("knowledge_docs", None, None),
    "onboard-seed-knowledge": ("knowledge_docs", None, None),
    # --- Planning & process (6) ---
    "flow-sprint-plan": ("planning_process", None, None),
    "flow-daily-retro": ("planning_process", None, None),
    "role-ba": ("planning_process", None, None),
    "role-product-manager": ("planning_process", None, None),
    "role-intake": ("planning_process", None, None),
    "role-developer": ("planning_process", None, None),
    # --- Reviewers (8) ---
    "role-tech-architect": ("reviewers", None, None),
    "role-qa-architect": ("reviewers", None, None),
    "role-security-officer": ("reviewers", None, None),
    "role-designer": ("reviewers", None, None),
    "role-mobile-reviewer": ("reviewers", None, None),
    "role-desktop-reviewer": ("reviewers", None, None),
    "role-ml-reviewer": ("reviewers", None, None),
    "role-game-balance-reviewer": ("reviewers", None, None),
}

# P4-07 — patterns flagged ``critical: true``. Everyone else gets
# ``critical: false`` explicit (the Coverage endpoint reads the field
# verbatim, so absence-as-false is not allowed).
CRITICAL_IDS = {
    "flow-pr-self-review",
    "scan-security-deps",
    "scan-license-deps",
    "scan-pii-leakage",
    "flow-incident-postmortem",
    "flow-release-notes",
    "flow-cert-compliance",
}

# Patterns intentionally NOT touched — system-internal, profile=silent.
SILENT_EXCLUDED = {
    "common-base",
    "common-kickoff",
    "op-retry-sweep",
    "op-stale-issue-sweep",
    "op-workflow-self-heal",
}

# Anchor: the top-level ``spec:`` line at column 0 of the YAML frontmatter.
# Frontmatter is the first ``---``-fenced block, so this regex is anchored
# inside that block by the calling code (we slice the file).
SPEC_LINE_RE = re.compile(r"^spec:\s*$", re.MULTILINE)
TOP_LEVEL_CATEGORY_RE = re.compile(r"^category:\s*\S", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _format_secondary(secondary: list[str]) -> str:
    return "[" + ", ".join(secondary) + "]"


def _build_block(
    category: str,
    subcategory: Optional[str],
    secondary: Optional[list[str]],
    critical: bool,
) -> str:
    lines = [f"category: {category}"]
    if subcategory is not None:
        lines.append(f"subcategory: {subcategory}")
    if secondary:
        lines.append(f"secondary_categories: {_format_secondary(secondary)}")
    lines.append(f"critical: {'true' if critical else 'false'}")
    return "\n".join(lines) + "\n"


def update_frontmatter(
    path: pathlib.Path,
    category: str,
    subcategory: Optional[str],
    secondary: Optional[list[str]],
    critical: bool,
) -> bool:
    """Insert the catalog metadata block immediately above ``spec:`` in
    the YAML frontmatter. Returns True iff the file changed.
    """
    raw = path.read_text(encoding="utf-8")
    fm_match = FRONTMATTER_RE.match(raw)
    if fm_match is None:
        print(f"WARN: {path} has no YAML frontmatter, skipping", file=sys.stderr)
        return False

    fm_text = fm_match.group(1)
    if TOP_LEVEL_CATEGORY_RE.search(fm_text):
        return False

    spec_match = SPEC_LINE_RE.search(fm_text)
    if spec_match is None:
        print(f"WARN: {path} frontmatter has no top-level spec:, skipping", file=sys.stderr)
        return False

    block = _build_block(category, subcategory, secondary, critical)

    insert_at = fm_match.start(1) + spec_match.start()
    new_raw = raw[:insert_at] + block + raw[insert_at:]
    path.write_text(new_raw, encoding="utf-8")
    return True


def main() -> int:
    on_disk = {
        p.name for p in ROOT.iterdir() if p.is_dir() and (p / "ARTIFACT.md").exists()
    }
    mapped = set(CATEGORY_MAP.keys())
    silent = SILENT_EXCLUDED & on_disk

    changed: list[str] = []
    skipped_existing: list[str] = []
    uncategorized: list[str] = []
    missing_from_disk = mapped - on_disk

    for pattern_id in sorted(on_disk):
        if pattern_id in SILENT_EXCLUDED:
            continue
        path = ROOT / pattern_id / "ARTIFACT.md"
        if pattern_id in CATEGORY_MAP:
            category, subcategory, secondary = CATEGORY_MAP[pattern_id]
        else:
            category, subcategory, secondary = ("uncategorized", None, None)
            uncategorized.append(pattern_id)
        critical = pattern_id in CRITICAL_IDS
        if update_frontmatter(path, category, subcategory, secondary, critical):
            changed.append(pattern_id)
            print(
                f"updated  {pattern_id:<32}  category={category}"
                + (f"/{subcategory}" if subcategory else "")
                + (f"  +secondary={secondary}" if secondary else "")
                + (f"  CRITICAL" if critical else "")
            )
        else:
            skipped_existing.append(pattern_id)
            print(f"skip     {pattern_id} (already has category)")

    print()
    print(f"== summary ==")
    print(f"  patterns on disk:           {len(on_disk)}")
    print(f"  silent-excluded (untouched): {len(silent)}  -> {sorted(silent)}")
    print(f"  updated:                    {len(changed)}")
    print(f"  already had category (noop):{len(skipped_existing)}")
    print(f"  uncategorized assigned:     {len(uncategorized)}  -> {uncategorized}")
    if missing_from_disk:
        print(f"  WARN: in map but missing on disk: {sorted(missing_from_disk)}")
    print()
    by_cat: dict[str, int] = {}
    for pid in changed + skipped_existing:
        if pid in CATEGORY_MAP:
            by_cat[CATEGORY_MAP[pid][0]] = by_cat.get(CATEGORY_MAP[pid][0], 0) + 1
        else:
            by_cat["uncategorized"] = by_cat.get("uncategorized", 0) + 1
    print("== by category (PRIMARY only) ==")
    for cat in sorted(by_cat):
        print(f"  {cat:<20} {by_cat[cat]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
