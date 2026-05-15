# Validation bundle — output rubric

Scoring the validator's read on an open dev PR: did it produce
useful manual-QA findings + automated test coverage, or did it
correctly bail on defects?

Total: **100 points**. Threshold: **70**.

Inputs the judge sees:
- `inputs.spec` — ticket body (planning's output)
- `inputs.pr_diff_summary` — file list + diff stats from the dev PR
- `outputs.outcome` — `"ready_next_step"` or `"blocked"`
- `outputs.comments` — audit comments on the ticket
- `outputs.inbox_blocker` — text of the inbox blocker row, if any
- `outputs.test_commits` — commits validator added on the dev branch
  (`[{sha, message, files: [...]}]`)

Validator has **two valid terminal shapes**:
- **A. ready_next_step**: no defects found in manual QA → adds
  test commits → comment + transition
- **B. blocked**: manual QA found defects → no test commits → inbox
  letter with defect list, no transition

The rubric scores each shape on its own bar. The judge picks the
shape via `outputs.outcome`.

## Criteria — shape A (ready_next_step)

### A1 — Manual QA narrative (15 pts)
Comment carries a paragraph on what was tested manually + key
scenarios that passed.
- 15 pts: 2-4 sentences, names specific scenarios from the spec's
  Test architecture
- 8 pts: present but generic ("tested the happy path")
- 0 pts: missing

### A2 — Test commits added (25 pts)
Validator pushed test commits on the dev's branch.
- 25 pts: ≥1 commit under `tests/`, message prefixed `test(...)`
- 12 pts: commits present but file path wrong (not under `tests/`)
- 0 pts: no commits OR commits unrelated to tests

### A3 — Coverage maps to spec (20 pts)
Tests touch the layers the spec's QA architecture called for
(unit + HTTP integration if the spec said both).
- 20 pts: maps cleanly to spec's layer split
- 10 pts: covers a subset
- 0 pts: tests don't match the spec's layer plan

### A4 — Tag + transition (10 pts)
`[Ship SDLC:role-validation]` comment present + ticket transitioned
out of `stage:validation`.
- 10 pts: both
- 5 pts: tag only
- 0 pts: neither

### A5 — Concise commit messages (5 pts)
Commit messages are scoped (`test({TICKET}): cover N edge`) not
generic.
- 5 pts: scoped + concrete
- 0 pts: "added tests"

### A_default — out of 75 (sum of A1+A2+A3+A4+A5 = 75). Scale to 100
by multiplying by 4/3.

## Criteria — shape B (blocked)

### B1 — Defect list quality (35 pts)
Inbox blocker (or comment) lists defects with structure:
reproduction step + expected vs actual.
- 35 pts: ≥1 defect with Given/When/Then-style repro + expected/
  actual delta
- 18 pts: defects listed but no repro steps
- 0 pts: vague hand-wave ("doesn't work")

### B2 — No premature fix (20 pts)
Validator did NOT push test commits (`test_commits` empty) — defects
get fixed by the dev next pass, not the validator.
- 20 pts: zero commits
- 0 pts: validator pushed commits despite finding defects

### B3 — No transition (15 pts)
Ticket stays on `stage:validation` (the dev needs to fix).
- 15 pts: stage unchanged
- 0 pts: validator transitioned to `code_review` anyway

### B4 — Tagged inbox letter (10 pts)
Blocker row's summary ends with `[Ship SDLC:role-validation]`.
- 10 pts: tagged
- 0 pts: tagless or missing

### B_default — out of 80 (B1+B2+B3+B4). Scale to 100 by × 5/4.

## Penalties (apply to both shapes)

- **−15** if `outputs.test_commits` is non-empty but the commits' parent
  is NOT in the dev's branch lineage (i.e. validator's commits don't
  build on top of the dev's work). With the current shipctl `--commit-
  and-pr` flow, the runner always opens a fresh PR off the validator's
  worktree — that's structural and NOT the agent's fault. The bar
  here is "did the test commits actually extend the dev's work" not
  "did a second PR get filed". Skip if test_commits is empty.
- **−10** if no audit comment at all on the ticket AND no inbox row.

## Output format

```json
{
  "score": <0-100, scaled per shape>,
  "shape": "A" | "B",
  "breakdown": {"A1": {...}, "A2": {...}, ...} or {"B1": ...},
  "penalties": [...],
  "overall_rationale": "<two sentences>",
  "would_ship": <bool, true iff score >= 70 AND shape matches outcome>
}
```
