## Daily review — 2026-06-20

_Snapshot generated 2026-06-20 06:45 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-332**: created by `scheduled_routine.ticket_created` @ 2026-06-19T06:30 (routine `daily`); `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×4); **`code_review` blocked** @ 2026-06-19T06:48 (`phase4:rejected:no_approval`); `overlay_frozen_skipped` at `validation` @ 2026-06-19T06:51 (`blocked` label)
- **ELS-333**: created by `scheduled_routine.ticket_created` @ 2026-06-20T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-20T06:44; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-19T09:02); inbox item "Daily digest — 2026-06-19" filed @ 2026-06-19T09:02
- **workspace_weekly**: weekly audit dispatched @ 2026-06-20T03:30; inbox item "Weekly audit — 2026-W25" filed @ 2026-06-20T03:32
- **0 PR merges** in window

### Stuck / attention

- **ELS-331**: `code_review` **blocked** @ 2026-06-18T06:56 (`no_approval` — PR #417 open, awaiting operator review); validation frozen @ 2026-06-18T07:00 (`overlay_frozen_skipped`, `blocked` label) — carryover outside 24h window
- **ELS-332**: `code_review` **blocked** @ 2026-06-19T06:48 (`no_approval` — PR #418 open, awaiting operator review); validation frozen @ 2026-06-19T06:51 (`overlay_frozen_skipped`, `blocked` label)
- Weekly audit inbox item created @ 2026-06-20T03:32 ("Weekly audit — 2026-W25")
- Development process health: **degraded** (25 blocked projection items — stale carryover; 1 fresh `outcome=blocked` finish on ELS-332 in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)
- FSM dispatch counts (24h): `planning`=2 dispatch / 2 finishes, `dev_implementation`=2 / 1, `qa_manual`=1 / 0, `validation`=1 / 1, `code_review`=1 / 1 blocked

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~2d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and approve **PR #417** (ELS-331 daily review for 2026-06-18) — clears the `no_approval` code_review block; all CI green.
2. Review and approve **PR #418** (ELS-332 daily review for 2026-06-19) — clears the `no_approval` code_review block; all CI green.
3. Triage **Weekly audit — 2026-W25** inbox report filed @ 2026-06-20T03:32; let **ELS-333** (this report) complete dev → QA.
