## Daily review — 2026-06-17

_Snapshot generated 2026-06-17 06:51 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-326**: `planning` → `dev_implementation` → `qa_manual` (`ready_next_step` ×2); validation bounced → `dev_implementation` → `qa_manual` (`ready_next_step` @ 2026-06-16T07:00); `overlay_frozen_skipped` at `validation` (blocked label)
- **ELS-329**: created by `scheduled_routine.ticket_created` @ 2026-06-17T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-17T06:49; **in-flight** — this report)
- **ELS-327**: PR #413 merged → Done @ 2026-06-16T11:26 (`pr_merge.tracker_done`)
- **ELS-328**: PR #414 merged → Done @ 2026-06-16T23:09 (`pr_merge.tracker_done`)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-16T09:04); inbox item "Daily digest — 2026-06-16" filed
- **workspace_weekly**: weekly audit dispatched @ 2026-06-17T03:30; finished with `finish_mismatch` @ 2026-06-17T03:34; inbox item "Weekly audit — 2026-W25" filed @ 2026-06-17T03:33
- **2 PR merges** (`pr_merge.tracker_done`): #413 (ELS-327), #414 (ELS-328)
- **3 tickets** moved via `dispatch.no_routine` (tracker poll, no agent finish): ELS-327, ELS-328 (×2)

### Stuck / attention

- **ELS-326**: validation `overlay_frozen_skipped` @ 2026-06-16T07:01 (`blocked` label froze pipeline at validation despite dev re-run completing)
- `workflow.coding_leaf.finish_mismatch` on `workspace_weekly` @ 2026-06-17T03:34 (expected `run_c48479acfac6540f`, actual `run_e869c82c086eaae6`)
- Weekly audit inbox item created @ 2026-06-17T03:33 ("Weekly audit — 2026-W25")
- Development process health: **degraded** (19 blocked projection items — mostly stale carryover; no fresh `outcome=blocked` finishes in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#412](https://github.com/ElMundiUA/ship/pull/412) | ELS-326 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and merge **PR #412** (ELS-326 daily review for 2026-06-16) — all CI green, awaiting operator review.
2. Triage **Weekly audit — 2026-W25** inbox report and investigate `workspace_weekly` `finish_mismatch` @ 2026-06-17T03:34 (run_id correlation break).
3. Clear **ELS-326** `blocked` label so validation can advance after PR #412 merges; let **ELS-329** (this report) complete dev → QA.
