# Developer bundle — output rubric

Scoring the PR the developer opened: title + body + diff stats +
audit comment. Diff content itself is judged through PR description's
claim vs. the spec's ACs.

Total: **100 points**. Threshold: **70**.

Inputs the judge sees:
- `inputs.spec` — the ticket body with BA/Architecture/Test plan
  (what the planning bundle wrote)
- `outputs.pr_title` — PR title from the sidecar's `pr.title`
- `outputs.pr_body` — PR body (with the runner-appended `Closes`
  footer + Ship run handle)
- `outputs.pr_diff_summary` — `{files_changed, additions,
  deletions, files: [paths]}` extracted from `gh pr view`
- `outputs.comments` — audit comments on the ticket

## Criteria

### C1 — PR title shape (10 pts)
Conventional-commit shape: `type({TICKET}): <one-line headline>`.
- 10 pts: matches `^(feat|fix|chore|test|refactor)\(<TICKET>\): .+$`,
  ≤72 chars, present-tense imperative
- 5 pts: ticket reference missing OR wrong type prefix
- 0 pts: no conventional prefix

### C2 — PR body — Summary section (15 pts)
A "## Summary" block 2-5 lines explaining what changed and why,
in the operator's voice (not the agent's).
- 15 pts: 2-5 lines, ties to the spec's Problem/Goal
- 8 pts: too short (one sentence) or too long (a wall of text)
- 0 pts: missing

### C3 — PR body — Test plan (15 pts)
"## Test plan" with concrete checklist items (`- [ ] …`).
- 15 pts: ≥3 items, each verifiable, maps to spec ACs
- 8 pts: present but vague ("- [ ] CI green")
- 0 pts: missing

### C4 — Closes footer (5 pts)
Runner appends `Closes {TICKET}` automatically. Don't subtract
if it's there even though the agent didn't write it — but if it's
missing, the runner pipeline broke.
- 5 pts: footer present
- 0 pts: missing

### C5 — Files touched — scope match (15 pts)
The set of files the dev changed matches the spec's "Impacted
components" list. No drift into unrelated files.
- 15 pts: files ⊆ impacted-components (allow +1 for tests)
- 8 pts: 1-2 extras outside the named list (config, README)
- 0 pts: large drift (>3 unrelated files)

### C6 — Tests delta (15 pts)
Diff includes at least one `tests/` file. New tests should map to
the spec's Test architecture criteria.
- 15 pts: tests added under `tests/` covering the new behaviour
- 8 pts: tests added but coverage thin (only happy path)
- 0 pts: no tests added

### C7 — Diff sanity (10 pts)
Reasonable size: ≤300 net additions for a single SDLC ticket;
≤8 files touched.
- 10 pts: within bounds
- 5 pts: 1.5× bounds — large but justifiable
- 0 pts: massive (>500 lines OR >12 files) without justification
  in PR body

### C8 — Code-changing finish discipline (10 pts)
Audit comment carries the PR URL (runner-spliced) AND the
`[Ship SDLC:role-developer]` tag.
- 10 pts: both present
- 5 pts: tag present, URL missing (runner failure path)
- 0 pts: neither

### C9 — Spec ↔ implementation traceability (5 pts)
PR body or commit message names which AC(s) it satisfies.
- 5 pts: explicit AC reference (e.g. "satisfies AC1, AC3")
- 0 pts: no traceability

## Penalties

- **−15** if the PR claims `ready_next_step` but the diff is empty
  (`additions == 0`) — should have been `blocked`.
- **−10** for each obvious bug visible in the diff (judge can flag
  via filename pattern + line content; conservative — only count
  when very confident).

## Output format

```json
{
  "score": <0-100>,
  "breakdown": {"C1": {...}, ..., "C9": {...}},
  "penalties": [...],
  "overall_rationale": "<two sentences>",
  "would_ship": <bool, true iff score >= 70 and no empty-diff penalty>
}
```
