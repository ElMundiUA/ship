## Daily review — 2026-06-12

_Snapshot generated 2026-06-12 06:49 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-263**: `planning` → `dev_implementation` (`ready_next_step`); `dev_implementation` → `qa_manual` (`ready_next_step`); `validation` → `code_review` (`ready_next_step`); then **11×** `code_review` **blocked** (2026-06-11 10:23 – 2026-06-12 01:29)
- **ELS-264**: `planning` → `dev_implementation` (`ready_next_step`); `dev_implementation` → `qa_manual` (`ready_next_step`)
- **ELS-265**: `planning` → `dev_implementation` (`ready_next_step`)
- **ELS-265**: created by `scheduled_routine.ticket_created` @ 2026-06-12T06:30:00Z (routine `daily`)
- **daily-digest**: workspace bundle ran @ 2026-06-11T09:03:49Z (`workspace_daily` routine)
- **7 PR merges** overnight (`pr_merge.tracker_done`): #365, #366, #367, #368, #369, #370, #371
- **25 tickets** moved Done via `dispatch.no_routine` (ELS-235..ELS-264 — tracker bulk close, no agent finish)

### Stuck / attention

- **ELS-263**: 11 blocked `code_review` finishes 2026-06-11T23:44 – 2026-06-12T01:29 (merged manually despite loop); worth noting as process noise
- **ELS-264**: last dispatch `qa_manual` @ 2026-06-11T20:46 — merged before QA stage completed in FSM
- FSM dispatch counts (24h): `code_review`=11, `planning`=4, `dev_implementation`=3, `qa_manual`=2
- Bundle drift: installed `0.38` vs current `0.41` on `ElMundiUA/ship`
- Engine health: `healthy=True`, `expired_unswept_locks=0`, `active_locks=1` (ELS-264 fix verified)
- Inbox: 0 new items (`counts_by_status.new=0`)
- Stall notifier fired 6× for `expired_not_swept` locks on 2026-06-11 (pre-ELS-264 deploy; resolved after merge)

### PRs

- **#329** / — (16d, awaiting review): **green**
- **#354** / — (10d, awaiting review): **green**
- **#355** / ELS-192 (7d, awaiting review): **green**
- **#356** / ELS-194 (7d, awaiting review): **green**
- **#357** / — (7d, awaiting review): **green**
- **#358** / ELS-202 (7d, awaiting review): **green**
- **#360** / — (3d, awaiting review): **green**
- **#364** / ELS-216 (1d): **red** — `playwright (landing smoke)` failed

### Next actions

1. Fix red CI on PR #364 (`playwright (landing smoke)`) so ELS-216 validation can proceed.
2. Bump installed bundle on `ElMundiUA/ship` from `0.38` → `0.41` (drift flagged on every daily review).
3. Triage or merge stale green PRs #354–#360 and #329 (>3 days open, awaiting human review).
