## Daily review — 2026-06-24

_Snapshot generated 2026-06-24 06:38 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-334**: `dev_implementation` → `validation` (`ready_next_step` ×2 @ 2026-06-23T06:39–06:42); `code_review` **blocked** (`no_approval` @ 2026-06-23T06:46)
- **ELS-335**: created by `scheduled_routine.ticket_created` @ 2026-06-24T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-24T06:35; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-23T09:03); inbox item "Daily digest — 2026-06-23" filed @ 2026-06-23T09:02
- **workspace_weekly**: weekly audit completed (`ready_next_step` @ 2026-06-24T03:33); inbox item "Weekly audit — 2026-W26" filed @ 2026-06-24T03:33; `workflow.coding_leaf.finish_mismatch` @ 2026-06-24T03:33 (expected `run_f68efd8a1321e96a`, actual `run_618b262ca0982d1a`)
- **0 PR merges** (`pr_merge.tracker_done`) in window

### Stuck / attention

- **ELS-331**: `code_review` **blocked** @ 2026-06-18T06:56 (no subsequent `ready_next_step`; PR #417 open)
- **ELS-332**: `code_review` **blocked** @ 2026-06-19T06:48 (no subsequent `ready_next_step`; PR #418 open)
- **ELS-333**: `code_review` **blocked** @ 2026-06-20T06:56 (no subsequent `ready_next_step`; PR #419 open)
- **ELS-334**: `code_review` **blocked** @ 2026-06-23T06:46 (no subsequent `ready_next_step`; PR #420 open — fresh in window)
- `workflow.coding_leaf.finish_mismatch` on `workspace_weekly` @ 2026-06-24T03:33 (expected `run_f68efd8a1321e96a`, actual `run_618b262ca0982d1a`)
- Weekly audit inbox item created @ 2026-06-24T03:33 ("Weekly audit — 2026-W26")
- Development process health: **degraded** (`blocked_count=25` — stale projection residue; fresh `outcome=blocked` finishes are ELS-331/332/333/334 above)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~6d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~5d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~4d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and merge the daily-review PR backlog **oldest first**: **#417** (ELS-331), then **#418**, **#419**, **#420** — all CI green, awaiting operator review.
2. Triage **Weekly audit — 2026-W26** inbox report and investigate `workspace_weekly` `finish_mismatch` @ 2026-06-24T03:33 (run_id correlation break).
3. Let **ELS-335** (this report) complete dev → QA so today's snapshot lands on `main`.
