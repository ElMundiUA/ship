#!/usr/bin/env python3
"""RFC-0008 Phase 1 migration — rename patterns and populate metadata.

Applies the canonical ``<category>-<name>`` rename across
``artifacts/patterns/`` and injects the RFC-0008 metadata fields
(``category`` / ``modes`` / ``default_trigger`` / ``inputs`` /
``enabled_on_install`` / ``include`` / optional ``lane_workflow``
override) into each ``ARTIFACT.md``.

Idempotent: running twice is a no-op once the catalog is on the new
naming. The script refuses to run if any *new* id already exists, so
partial runs can be resumed without corrupting the tree.

Does NOT restamp ``content_sha256`` — that's delegated to
``scripts/restamp_artifact_shas.py`` so this script stays focused on
the id / metadata migration and the SHA regeneration can be audited
separately.

Usage::

    python scripts/rfc_0008_rename.py [--dry-run]

Exits non-zero when the tree is in an unexpected shape (stray old
ids, collisions, missing ARTIFACT.md). That's on purpose — we'd rather
fail loud than silently produce a half-renamed catalog.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PATTERNS = REPO / "artifacts" / "patterns"


@dataclass(frozen=True)
class Plan:
    """Declarative description of one rename.

    ``trigger`` / ``inputs`` / ``enabled_on_install`` / ``lane_workflow``
    are emitted verbatim into ``spec:``. ``include`` lists the
    ``common-*`` pattern ids whose body the host pattern wants
    prepended at render time.
    """

    old_id: str
    new_id: str
    category: str            # role | flow | scan | op | onboard | common
    modes: tuple[str, ...]   # subset of ("lane", "request"); () for common
    install_subdir: str      # prompts/<install_subdir>/<install_leaf>.md
    install_leaf: str
    group: str               # frontmatter `group:` override
    trigger: dict[str, Any] | None = None
    inputs: tuple[dict[str, Any], ...] = ()
    enabled_on_install: dict[str, Any] | None = None
    lane_workflow: str | None = None       # explicit override; None = auto
    include: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The 21 patterns currently in ``artifacts/patterns/`` and where they go.
#
# Metadata here is the *authoritative* RFC-0008 record for each pattern.
# When in doubt the rule is "every role/flow/scan gets both modes
# unless the trigger makes ad-hoc invocation nonsensical (PR event-gated
# flows can't run as a request — they need the PR)". ``op-*`` is cron-
# only; ``onboard-*`` is request-only; ``common-*`` is neither.
# ---------------------------------------------------------------------------


# Presets from backend/app/services/default_pipelines.py PRESET_ENABLED_KINDS.
# Readers should treat missing presets as "default: False" (see
# CatalogArtifact.enabled_for_preset). Only non-default entries appear
# below so the frontmatter stays focused.
P_ALL_EXCEPT_MARKETING = {"web-app", "api-backend", "mobile-app", "monorepo"}
P_WEB_AND_MONO = {"web-app", "monorepo"}
P_ALL = {"web-app", "api-backend", "mobile-app", "cli", "monorepo", "marketing"}


def _enabled_for(presets: set[str], *, default: bool = False) -> dict[str, Any]:
    return {
        "default": default,
        "presets": {name: True for name in sorted(presets)},
    }


RENAMES: tuple[Plan, ...] = (
    # ----- common-* (non-executable, included by others) -----
    Plan(
        old_id="cloud-base",
        new_id="common-base",
        category="common",
        modes=(),
        install_subdir="common",
        install_leaf="_base",
        group="common",
    ),
    Plan(
        old_id="kickoff",
        new_id="common-kickoff",
        category="common",
        modes=(),
        install_subdir="common",
        install_leaf="kickoff",
        group="common",
    ),
    # ----- role-* -----
    Plan(
        old_id="cloud-intake",
        new_id="role-intake",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="intake",
        group="role",
        trigger={"kind": "event", "event": "issues.opened,reopened"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING, default=True),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-clarification",
        new_id="role-clarification",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="clarification",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "needs-clarification"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-ba",
        new_id="role-ba",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="ba",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:ba"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
            {"name": "depth", "type": "enum", "values": ["quick", "thorough"], "default": "thorough"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-developer",
        new_id="role-developer",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="developer",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:developer"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING, default=True),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-qa-architect",
        new_id="role-qa-architect",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="qa-architect",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:qa"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-tech-architect",
        new_id="role-tech-architect",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="tech-architect",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:architect"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_WEB_AND_MONO),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-security-officer",
        new_id="role-security-officer",
        category="role",
        modes=("lane", "request"),
        install_subdir="role",
        install_leaf="security-officer",
        group="role",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:security"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_WEB_AND_MONO),
        include=("common-base",),
    ),
    # ----- flow-* -----
    Plan(
        old_id="catalog-a5-pr-self-review",
        new_id="flow-pr-self-review",
        category="flow",
        modes=("lane",),
        install_subdir="flow",
        install_leaf="pr-self-review",
        group="flow",
        trigger={
            "kind": "event",
            "event": "pull_request",
            "pattern": "**",
            "idempotency_key": "{{pr}}",
        },
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING, default=True),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a6-check-failure-recovery",
        new_id="flow-check-failure-recovery",
        category="flow",
        modes=("lane",),
        install_subdir="flow",
        install_leaf="check-failure-recovery",
        group="flow",
        trigger={
            "kind": "event",
            "event": "check_run.completed",
            "pattern": "conclusion:failure",
            "idempotency_key": "{{check_run}}",
        },
        enabled_on_install=_enabled_for(P_WEB_AND_MONO),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a7-preview-validation",
        new_id="flow-preview-validation",
        category="flow",
        modes=("lane",),
        install_subdir="flow",
        install_leaf="preview-validation",
        group="flow",
        trigger={
            "kind": "event",
            "event": "deployment_status",
            "pattern": "environment:preview,state:success",
            "idempotency_key": "{{deployment}}",
        },
        enabled_on_install=_enabled_for({"web-app", "monorepo"}),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a8-preview-failure-recovery",
        new_id="flow-preview-failure-recovery",
        category="flow",
        modes=("lane",),
        install_subdir="flow",
        install_leaf="preview-failure-recovery",
        group="flow",
        trigger={
            "kind": "event",
            "event": "deployment_status",
            "pattern": "environment:preview,state:failure",
            "idempotency_key": "{{deployment}}",
        },
        enabled_on_install=_enabled_for({"web-app", "monorepo"}),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a9-qa",
        new_id="flow-qa-acceptance",
        category="flow",
        modes=("lane", "request"),
        install_subdir="flow",
        install_leaf="qa-acceptance",
        group="flow",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "ready:qa"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Issue URL"},
        ),
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a10-human-handoff",
        new_id="flow-human-handoff",
        category="flow",
        modes=("lane",),
        install_subdir="flow",
        install_leaf="human-handoff",
        group="flow",
        trigger={"kind": "event", "event": "issues.labeled", "pattern": "needs-human"},
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a12-learning",
        new_id="flow-learning-capture",
        category="flow",
        modes=("lane", "request"),
        install_subdir="flow",
        install_leaf="learning-capture",
        group="flow",
        trigger={"kind": "event", "event": "issues.closed", "idempotency_key": "{{issue}}"},
        inputs=(
            {"name": "issue_url", "type": "url", "required": True, "hint": "Closed issue URL"},
        ),
        enabled_on_install=_enabled_for(P_WEB_AND_MONO),
        include=("common-base",),
    ),
    Plan(
        old_id="catalog-a13-daily-retro",
        new_id="flow-daily-retro",
        category="flow",
        modes=("lane", "request"),
        install_subdir="flow",
        install_leaf="daily-retro",
        group="flow",
        trigger={"kind": "schedule", "cron": "0 9 * * 1-5"},
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING, default=True),
        include=("common-base",),
    ),
    # ----- op-* -----
    Plan(
        old_id="catalog-a11-retry-sweep",
        new_id="op-retry-sweep",
        category="op",
        modes=("lane",),
        install_subdir="op",
        install_leaf="retry-sweep",
        group="op",
        trigger={"kind": "schedule", "cron": "0 */6 * * *"},
        enabled_on_install=_enabled_for(P_ALL_EXCEPT_MARKETING),
        include=("common-base",),
    ),
    Plan(
        old_id="cloud-workflow-self-heal",
        new_id="op-workflow-self-heal",
        category="op",
        modes=("lane",),
        install_subdir="op",
        install_leaf="workflow-self-heal",
        group="op",
        trigger={"kind": "schedule", "cron": "0 4 * * *"},
        enabled_on_install=_enabled_for(P_WEB_AND_MONO),
        include=("common-base",),
    ),
    # ----- onboard-* -----
    Plan(
        old_id="adopt-ship-generic",
        new_id="onboard-adopt",
        category="onboard",
        modes=("request",),
        install_subdir="onboard",
        install_leaf="adopt",
        group="onboard",
        inputs=(),
        enabled_on_install=None,  # Never auto-enabled; always operator-invoked
    ),
    Plan(
        old_id="seed-knowledge-starters",
        new_id="onboard-seed-knowledge",
        category="onboard",
        modes=("request",),
        install_subdir="onboard",
        install_leaf="seed-knowledge",
        group="onboard",
        inputs=(),
        enabled_on_install=None,
    ),
)


# ---------------------------------------------------------------------------
# Frontmatter rewriting helpers
# ---------------------------------------------------------------------------


FRONTMATTER_RE = re.compile(r"^---\n(?P<fm>.*?)\n---\n(?P<body>.*)$", re.DOTALL)


def _load(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise RuntimeError(f"{path} has no frontmatter")
    return match.group("fm"), match.group("body")


def _strip_spec_block(fm: str) -> tuple[str, dict[str, str]]:
    """Remove ``spec:`` block from frontmatter text.

    Returns the frontmatter with the whole ``spec:`` block stripped and
    a dict of the *primitive* scalar keys that were in it (``role`` /
    ``template`` — we preserve those into the rewritten spec). Nested
    structures are discarded; RFC-0008 replaces them anyway.
    """
    lines = fm.splitlines()
    out: list[str] = []
    in_spec = False
    preserved: dict[str, str] = {}
    for line in lines:
        if not in_spec:
            if line.rstrip() == "spec:":
                in_spec = True
                continue
            out.append(line)
            continue
        # Inside spec block: drop everything until the indent drops.
        stripped = line.lstrip()
        if not line or stripped == line or not line.startswith("  "):
            # Back to top-level — this line starts a new top-level key.
            in_spec = False
            out.append(line)
            continue
        # Inside spec. Capture simple scalars like ``  role: foo`` /
        # ``  template: true`` so the rewritten spec can preserve them.
        m = re.match(r"^  (role|template):\s*(.+?)\s*$", line)
        if m:
            preserved[m.group(1)] = m.group(2).strip()
    return "\n".join(out).rstrip("\n"), preserved


def _render_spec(plan: Plan, preserved: dict[str, str]) -> str:
    """Emit the new ``spec:`` block as YAML lines."""
    lines: list[str] = ["spec:"]
    lines.append(
        f"  install_target: prompts/{plan.install_subdir}/{plan.install_leaf}.md"
    )
    lines.append(f"  category: {plan.category}")
    modes = "[" + ", ".join(plan.modes) + "]" if plan.modes else "[]"
    lines.append(f"  modes: {modes}")
    if plan.include:
        incl = "[" + ", ".join(plan.include) + "]"
        lines.append(f"  include: {incl}")
    if plan.trigger:
        lines.append("  default_trigger:")
        for key, value in plan.trigger.items():
            lines.append(f"    {key}: {_render_scalar(value)}")
    if plan.lane_workflow:
        lines.append(f"  lane_workflow: {plan.lane_workflow}")
    if plan.inputs:
        lines.append("  inputs:")
        for inp in plan.inputs:
            lines.append(f"    - name: {inp['name']}")
            for key, value in inp.items():
                if key == "name":
                    continue
                lines.append(f"      {key}: {_render_scalar(value)}")
    if plan.enabled_on_install:
        lines.append("  enabled_on_install:")
        lines.append(
            f"    default: {_render_scalar(plan.enabled_on_install['default'])}"
        )
        presets = plan.enabled_on_install.get("presets") or {}
        if presets:
            lines.append("    presets:")
            for name in sorted(presets):
                lines.append(f"      {name}: {_render_scalar(presets[name])}")
    # Preserve legacy ``template: true`` / ``role: ...`` keys — they
    # feed existing prompt-rendering code that we haven't pivoted yet.
    for key in ("template", "role"):
        if key in preserved:
            lines.append(f"  {key}: {preserved[key]}")
    return "\n".join(lines)


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_render_scalar(v) for v in value) + "]"
    text = str(value)
    if any(ch in text for ch in (" ", "*", "{", "}", "+", "/", ":", ",", "#")):
        return f'"{text}"'
    return text


def _rewrite_top_level(fm: str, *, new_id: str, new_group: str) -> str:
    """Update ``id:`` and ``group:`` scalar lines in the frontmatter text."""
    out: list[str] = []
    seen_id = seen_group = False
    for line in fm.splitlines():
        if not seen_id and line.startswith("id:"):
            out.append(f"id: {new_id}")
            seen_id = True
            continue
        if not seen_group and line.startswith("group:"):
            out.append(f"group: {new_group}")
            seen_group = True
            continue
        out.append(line)
    if not seen_id:
        out.insert(1, f"id: {new_id}")
    if not seen_group:
        out.append(f"group: {new_group}")
    return "\n".join(out)


def _apply(plan: Plan, *, dry_run: bool) -> str:
    old_dir = PATTERNS / plan.old_id
    new_dir = PATTERNS / plan.new_id
    if old_dir == new_dir:
        return f"[skip ] {plan.old_id} (already renamed)"
    if not old_dir.is_dir():
        if new_dir.is_dir():
            return f"[skip ] {plan.old_id} → {plan.new_id} (new already exists)"
        raise RuntimeError(f"missing artifacts/patterns/{plan.old_id}/")
    if new_dir.exists():
        raise RuntimeError(
            f"rename collision: {plan.new_id} already exists next to {plan.old_id}"
        )

    artifact = old_dir / "ARTIFACT.md"
    if not artifact.is_file():
        raise RuntimeError(f"{old_dir}/ARTIFACT.md missing")

    fm, body = _load(artifact)
    fm_no_spec, preserved = _strip_spec_block(fm)
    new_fm_top = _rewrite_top_level(
        fm_no_spec, new_id=plan.new_id, new_group=plan.group
    )
    new_spec = _render_spec(plan, preserved)
    new_text = "---\n" + new_fm_top + "\n" + new_spec + "\n---\n" + body

    if dry_run:
        return f"[plan ] {plan.old_id} → {plan.new_id}"
    # Move the directory first, then rewrite the ARTIFACT.md inside it.
    shutil.move(str(old_dir), str(new_dir))
    (new_dir / "ARTIFACT.md").write_text(new_text, encoding="utf-8")
    return f"[done ] {plan.old_id} → {plan.new_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames; do not touch the filesystem.",
    )
    args = parser.parse_args()

    # Sanity: every ``old_id`` is unique; every ``new_id`` is unique.
    olds = [p.old_id for p in RENAMES]
    news = [p.new_id for p in RENAMES]
    assert len(set(olds)) == len(olds), "duplicate old_id in RENAMES"
    assert len(set(news)) == len(news), "duplicate new_id in RENAMES"

    errors: list[str] = []
    for plan in RENAMES:
        try:
            print(_apply(plan, dry_run=args.dry_run))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{plan.old_id}: {exc}")
            print(f"[FAIL ] {plan.old_id} → {plan.new_id}: {exc}")

    if errors:
        print("\nErrors:", *errors, sep="\n  - ")
        return 1
    print(f"\nrenamed {len(RENAMES)} patterns (dry_run={args.dry_run}).")
    print("Next: run scripts/restamp_artifact_shas.py to refresh content_sha256 lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
