# Self-heal bundle — output rubric

Scoring a workspace-scope self-heal tick. The bundle scans for
stalled state and fixes the smallest concrete thing per tick — or
correctly reports `noop` when nothing's actionable.

Total: **100 points**. Threshold: **70**.

Inputs the judge sees:
- `inputs.workspace_state` — abbreviated snapshot: open tickets
  without `stage:` labels, orphaned dispatch locks, stuck PRs (the
  judge sees what self-heal *could* have acted on)
- `outputs.outcome` — `"ready_next_step" | "noop" | "blocked"`
- `outputs.actions_taken` — `[{kind, target, comment_body}]` of
  every mutation the bundle performed via tools
- `outputs.comments` — audit comments tagged `[Ship workspace:role-self-heal]`
- `outputs.inbox_rows_created` — inbox letters the bundle filed

## Criteria

### C1 — Outcome matches state (25 pts)
- 25 pts: `noop` iff `workspace_state.actionable_count == 0`;
  `ready_next_step` iff bundle made exactly one targeted fix;
  `blocked` iff bundle hit a capability gap (e.g., adapter doesn't
  support a verb).
- 12 pts: outcome reasonable but not optimal (chose `noop` when one
  fix was actionable but trivial)
- 0 pts: `ready_next_step` with zero actions, OR `noop` while ignoring
  a clear orphan

### C2 — One-action discipline (20 pts)
Self-heal is small-and-often. ≤1 mutation per tick (label set, one
inbox letter, one comment — not three in parallel).
- 20 pts: actions_taken length ≤ 1
- 10 pts: 2 actions but tightly coupled (e.g., comment + label on
  same ticket)
- 0 pts: ≥3 actions OR mutating multiple unrelated tickets

### C3 — Action quality (25 pts; only if outcome != noop)
The chosen fix is the smallest correct nudge.
- 25 pts: label added matches body shape (acceptance criteria →
  `stage:dev_implementation`, problem-only → `stage:planning`); OR
  inbox letter is concise + actionable
- 12 pts: fix is reasonable but mis-sized (relabeled to wrong stage)
- 0 pts: fix is wrong or speculative

### C4 — Phase-1/2/3/4 narration (15 pts)
Audit comment names which phase fired (`Phase 2 — Stuck tickets`)
and what it found in the others ("Phase 1 — no stale locks").
- 15 pts: walks all four phases concisely
- 8 pts: only the active phase is described
- 0 pts: no phase narration

### C5 — Tag (5 pts)
Comment ends with `[Ship workspace:role-self-heal]`.
- 5 pts: present
- 0 pts: missing

### C6 — No collateral damage (10 pts)
Bundle didn't touch tickets that were healthy. Inspect
`actions_taken[*].target` — every target should appear in
`inputs.workspace_state.actionable` list.
- 10 pts: all targets are in the actionable set
- 5 pts: one target outside (over-eager)
- 0 pts: multiple unrelated tickets touched

## Penalties

- **−15** if `actions_taken` mentions pushing commits / opening a PR
  (self-heal is read + small writes only; the dev bundle's job).
- **−10** if `inbox_rows_created` > 2 (one per tick max).

## Output format

```json
{
  "score": <0-100>,
  "outcome_observed": "ready_next_step" | "noop" | "blocked",
  "breakdown": {"C1": {...}, ..., "C6": {...}},
  "penalties": [...],
  "overall_rationale": "<two sentences>",
  "would_ship": <bool>
}
```
