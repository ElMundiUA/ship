#!/usr/bin/env python3
"""P3-07 — append a uniform "Reporting" section to the top-10 most-used
patterns so their agents emit RFC-0010 §RunSummary outcomes via
``shipctl callback`` instead of relying on the FE-side default
formatter.

The outcome_text examples are pattern-specific so the Runs list reads
as outcomes ("3 issues found · 1 PR opened"), not events.

Run from repo root::

    python scripts/p3_07_append_reporting.py

Idempotent: re-running on a pattern that already contains the
"## Reporting" anchor is a no-op.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts" / "patterns"

# Tuples: (pattern_id, outcome_text_example, callback_args_block).
# Each callback block is the literal shell snippet the Reporting section
# embeds. Keep them short (≤6 lines) — this is a recipe, not a tutorial.
TOP_10 = [
    (
        "flow-pr-self-review",
        "Reviewed PR · 3 suggestions · 1 fix applied",
        """shipctl callback --status ok \\
  --outcome-text "Reviewed PR · {N} suggestions · {N} fix(es) applied" \\
  --findings-count {total_suggestions} \\
  --artifact comment:"PR self-review summary":"{pr_comment_url}" \\
  [--artifact pr:"Auto-fix: {fix_title}":"{commit_url}"]""",
    ),
    (
        "flow-blast-radius",
        "Blast radius: 4 services · 2 owners pinged",
        """shipctl callback --status ok \\
  --outcome-text "Blast radius: {N} services · {M} owners pinged" \\
  --findings-count {affected_services_count} \\
  --severity {severity}={count} \\
  --artifact comment:"Blast-radius report":"{pr_comment_url}\"""",
    ),
    (
        "scan-test-coverage",
        "Coverage 78% (-2.1% from baseline)",
        """shipctl callback --status ok \\
  --outcome-text "Coverage {pct}% ({signed_delta}% from baseline)" \\
  --findings-count {uncovered_files} \\
  --severity {severity}={count} \\
  --artifact doc:"Coverage report":"{report_url}\"""",
    ),
    (
        "flow-qa-acceptance",
        "3 acceptance gaps · 1 blocker",
        """shipctl callback --status ok \\
  --outcome-text "{N} acceptance gap(s) · {M} blocker(s)" \\
  --findings-count {gap_count} \\
  --severity high={blockers} --severity medium={non_blockers} \\
  --artifact comment:"QA acceptance review":"{pr_comment_url}\"""",
    ),
    (
        "scan-tech-debt",
        "12 debt items · 8% of touched code",
        """shipctl callback --status ok \\
  --outcome-text "{N} debt item(s) · {pct}% of touched code" \\
  --findings-count {debt_items} \\
  --severity {severity}={count} \\
  --artifact doc:"Tech-debt report":"{report_url}\"""",
    ),
    (
        "scan-security-deps",
        "5 vulnerable deps (1 critical · 2 high)",
        """shipctl callback --status ok \\
  --outcome-text "{N} vulnerable dep(s) ({critical} critical · {high} high)" \\
  --findings-count {total_vulns} \\
  --severity critical={n_crit} --severity high={n_high} \\
  --severity medium={n_med} --severity low={n_low} \\
  [--requires-approval --approval-payload '{"kind":"upgrade_deps","prs":[...]}']""",
    ),
    (
        "scan-license-deps",
        "2 license issues (1 GPL contagion)",
        """shipctl callback --status ok \\
  --outcome-text "{N} license issue(s) ({M} blocking)" \\
  --findings-count {issue_count} \\
  --severity high={blocking} --severity low={advisory} \\
  --artifact doc:"License report":"{report_url}\"""",
    ),
    (
        "scan-api-contract",
        "3 contract drifts (1 breaking)",
        """shipctl callback --status ok \\
  --outcome-text "{N} contract drift(s) ({M} breaking)" \\
  --findings-count {drift_count} \\
  --severity critical={breaking} --severity medium={non_breaking} \\
  --artifact doc:"OpenAPI diff":"{diff_url}\"""",
    ),
    (
        "scan-docs-freshness",
        "7 stale docs · 2 critical",
        """shipctl callback --status ok \\
  --outcome-text "{N} stale doc(s) ({M} critical)" \\
  --findings-count {stale_count} \\
  --severity critical={critical} --severity medium={moderate} \\
  --artifact doc:"Freshness audit":"{report_url}\"""",
    ),
    (
        "flow-check-failure-recovery",
        "4 CI failures triaged · 2 fixes proposed",
        """shipctl callback --status ok \\
  --outcome-text "{N} CI failure(s) triaged · {M} fix(es) proposed" \\
  --findings-count {failure_count} \\
  --severity high={blocking_failures} --severity medium={flaky_failures} \\
  [--artifact pr:"Fix: {failure_summary}":"{fix_pr_url}"] \\
  [--requires-approval --approval-payload '{"kind":"merge_recovery_pr"}']""",
    ),
]

ANCHOR = "## Reporting"

REPORTING_TEMPLATE = """

---

## Reporting

When you finish, call ``shipctl callback`` so Ship can render an
outcome-first row in the Runs list and link any escalations into the
Inbox. The ``--outcome-text`` you author here is what operators see in
``/runs`` — keep it concise and concrete, no "completed successfully"
filler.

For this play, a typical outcome looks like: **"{example}"**.

```bash
{callback}
```

Replace ``{{...}}`` placeholders with values you collected during the
run. Severities are aggregated into ``findings_by_severity`` — use the
buckets the operator filters on (``low``/``medium``/``high``/``critical``)
rather than custom labels. Skip flags whose value would be 0 or empty.
"""


def append_reporting(path: pathlib.Path, example: str, callback: str) -> bool:
    """Returns True if the file changed."""
    text = path.read_text(encoding="utf-8")
    if ANCHOR in text:
        return False
    text = text.rstrip("\n")
    text += REPORTING_TEMPLATE.format(example=example, callback=callback)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    changed = 0
    for pattern_id, example, callback in TOP_10:
        path = ROOT / pattern_id / "ARTIFACT.md"
        if not path.exists():
            print(f"WARN: {path} not found, skipping", file=sys.stderr)
            continue
        if append_reporting(path, example, callback):
            changed += 1
            print(f"appended Reporting section to {pattern_id}")
        else:
            print(f"skip {pattern_id} (already has Reporting)")
    print(f"\n{changed}/{len(TOP_10)} patterns updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
