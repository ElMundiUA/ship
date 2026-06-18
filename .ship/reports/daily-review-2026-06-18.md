## Daily review — 2026-06-18

_Snapshot generated 2026-06-18 06:46 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-329**: created by `scheduled_routine.ticket_created` @ 2026-06-17T06:30 (routine `daily`); `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×3); **`code_review` blocked** ×2 @ 2026-06-17T06:59 (`phase4:rejected:no_install`) and @ 2026-06-17T20:45 (`phase4:rejected:no_approval`); overlay unfreeze resumed @ 2026-06-17T20:43 after ELS-330 merge
- **ELS-330**: PR #416 merged → Done @ 2026-06-17T20:20 (`pr_merge.tracker_done`)
- **ELS-331**: created by `scheduled_routine.ticket_created` @ 2026-06-18T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-18T06:43; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-17T09:05); inbox item "Daily digest — 2026-06-17" filed
- **workspace_weekly**: weekly audit dispatched @ 2026-06-17T03:30 and @ 2026-06-18T03:30; finished with `finish_mismatch` @ 2026-06-17T03:34 and @ 2026-06-18T03:33; inbox item "Weekly audit — 2026-W25" filed (×2)
- **1 PR merge** (`pr_merge.tracker_done`): #416 (ELS-330)
- **4 tickets** polled via `dispatch.no_routine` (tracker sync, no agent finish)

### Stuck / attention

- **ELS-329**: `code_review` **blocked** @ 2026-06-17T06:59 (`no_install` — resolved by PR #416 merge); **blocked** again @ 2026-06-17T20:45 (`no_approval` — awaiting operator review of PR #415)
- `workflow.coding_leaf.finish_mismatch` on `workspace_weekly` @ 2026-06-18T03:33 (expected `run_a862cfa3560ed6eb`, actual `run_da5b60d293f1e4cf`); prior mismatch @ 2026-06-17T03:34 still in window
- Weekly audit inbox item created @ 2026-06-18T03:33 ("Weekly audit — 2026-W25")
- Development process health: **degraded** (23 blocked projection items — stale carryover; 2 fresh `outcome=blocked` finishes on ELS-329 in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)
- FSM dispatch counts (24h): `planning`=2, `dev_implementation`=2, `code_review`=2, `qa_manual`=1

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#415](https://github.com/ElMundiUA/ship/pull/415) | ELS-329 | ~1d | awaiting review | **green** (7/7 checks) |
| [#412](https://github.com/ElMundiUA/ship/pull/412) | ELS-326 | ~2d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and approve **PR #415** (ELS-329 daily review for 2026-06-17) — clears the `no_approval` code_review block; all CI green.
2. Review and merge **PR #412** (ELS-326 daily review for 2026-06-16) — all CI green, awaiting operator review.
3. Triage **Weekly audit — 2026-W25** inbox report and investigate `workspace_weekly` `finish_mismatch` @ 2026-06-18T03:33 (run_id correlation break).
