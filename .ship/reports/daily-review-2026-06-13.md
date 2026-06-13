## Daily review — 2026-06-13

_Snapshot generated 2026-06-13 06:35 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-265**: `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×4); re-run `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review`; then **`code_review` blocked** @ 2026-06-12T07:07
- **ELS-201**: `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×4)
- **ELS-200**: `planning` → `dev_implementation` → `qa_manual` (`ready_next_step` ×2)
- **ELS-194**: `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review`; then **`code_review` blocked** @ 2026-06-12T09:28
- **ELS-277**: `planning` → `dev_implementation` → `qa_manual` → `validation` → **`dev_implementation`** (`ready_next_step` ×3 + validation bounce)
- **ELS-278**: `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×4)
- **ELS-266**: `decomposition` → `planning_done` (`ready_next_step`)
- **ELS-279**: `decomposition` → `planning_done` (`ready_next_step`)
- **ELS-295**: created by `scheduled_routine.ticket_created` @ 2026-06-13T06:30:01Z (routine `daily`); `planning` → `dev_implementation` (`ready_next_step`)
- **daily-digest**: workspace bundle ran @ 2026-06-12T09:05:17Z (`workspace_daily` routine)
- **weekly-audit**: workspace bundle ran @ 2026-06-13T03:32:19Z (`workspace_weekly` routine; `finish_mismatch` on run_id correlation)
- **11 PR merges** (`pr_merge.tracker_done`): #355, #356, #358, #364, #373, #375, #376, #379, #380, #381, #382
- **24 tickets** moved Done via `tracker.event.received` (Review/Backlog → Done, no agent finish)

### Stuck / attention

- **ELS-265**: `code_review` **blocked** @ 2026-06-12T07:07 (validation passed twice; blocked on second code_review pass)
- **ELS-194**: `code_review` **blocked** @ 2026-06-12T09:28 (blocker notified to inbox + Linear)
- **ELS-277**: validation bounced to `dev_implementation` @ 2026-06-12T11:09 (run_id correlation issue; `blocked` label overlay skips at planning + dev_implementation)
- **ELS-200**: `blocked` label caused `overlay_frozen_skipped` at `validation` — pipeline frozen despite planning/dev finishes
- Blocked-label overlay skips (12×): ELS-183, ELS-184, ELS-192, ELS-202, ELS-265 (`needs:clarification`), ELS-276, ELS-277, ELS-278 (×2)
- `workflow.coding_leaf.finish_mismatch` on `workspace_weekly` @ 2026-06-13T03:32 (expected `run_768b6df9083abcca`, actual `run_f271b1f47f946416`)
- Weekly audit inbox item created @ 2026-06-13T03:32 ("Weekly audit — 2026-W24")
- FSM dispatch counts (24h): `planning`=23, `dev_implementation`=9, `qa_manual`=7, `code_review`=5, `wbs`=2, `task_intake`=2, `validation`=1
- Bundle drift: installed `0.38` vs current `0.42` on `ElMundiUA/ship`
- Inbox: 0 new items (`counts_by_status.new=0`)

### PRs

PR queue clear — no open PRs on `ElMundiUA/ship`.

### Next actions

1. Bump installed bundle on `ElMundiUA/ship` from `0.38` → `0.42` (drift flagged on every daily review).
2. Review **Weekly audit — 2026-W24** inbox report and investigate `workspace_weekly` `finish_mismatch` (run_id correlation break).
3. Triage **ELS-265** and **ELS-194** `code_review` blocked finishes — both stalled after validation yesterday.
