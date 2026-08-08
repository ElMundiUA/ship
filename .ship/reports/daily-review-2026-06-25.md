## Daily review — 2026-06-25

_Snapshot generated 2026-06-25 06:47 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-335**: `validation` → `code_review` (`ready_next_step` @ 2026-06-24T06:43); `code_review` **blocked** (`no_approval` @ 2026-06-24T06:50)
- **ELS-336**: created by `scheduled_routine.ticket_created` @ 2026-06-25T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-25T06:44; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-24T09:04); inbox item "Daily digest — 2026-06-24" filed @ 2026-06-24T09:03
- **workspace_weekly**: weekly audit completed (`ready_next_step` @ 2026-06-25T03:34); inbox item "Weekly audit — 2026-W26" filed @ 2026-06-25T03:33; `workflow.coding_leaf.finish_mismatch` @ 2026-06-25T03:34 (expected `run_631473d36c0d89d0`, actual `run_beb767e21e8f35e5`)
- **0 PR merges** (`pr_merge.tracker_done`) in window

### Stuck / attention

- **ELS-331**: `code_review` **blocked** @ 2026-06-18T06:56 (no subsequent `ready_next_step`; PR #417 open)
- **ELS-332**: `code_review` **blocked** @ 2026-06-19T06:48 (no subsequent `ready_next_step`; PR #418 open)
- **ELS-333**: `code_review` **blocked** @ 2026-06-20T06:56 (no subsequent `ready_next_step`; PR #419 open)
- **ELS-334**: `code_review` **blocked** @ 2026-06-23T06:46 (no subsequent `ready_next_step`; PR #420 open)
- **ELS-335**: `code_review` **blocked** @ 2026-06-24T06:50 (no subsequent `ready_next_step`; PR #421 open — fresh in window)
- `workflow.coding_leaf.finish_mismatch` on `workspace_weekly` @ 2026-06-25T03:34 (expected `run_631473d36c0d89d0`, actual `run_beb767e21e8f35e5`)
- Weekly audit inbox item created @ 2026-06-25T03:33 ("Weekly audit — 2026-W26")
- Development process health: **degraded** (`blocked_count=25` — stale projection residue; fresh `outcome=blocked` finishes are ELS-331–335 above)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: `actionable_new=41`, `reports_new=25` (`counts_by_status.new=41`; `/inbox` list may return empty — use counts endpoint)

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~7d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~6d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~5d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~2d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and merge the daily-review PR backlog **oldest first**: **#417** (ELS-331), then **#418**, **#419**, **#420**, **#421** — all CI green, awaiting operator review.
2. Triage **Weekly audit — 2026-W26** inbox report and investigate `workspace_weekly` `finish_mismatch` @ 2026-06-25T03:34 (run_id correlation break).
3. Let **ELS-336** (this report) complete dev → QA so today's snapshot lands on `main`.
