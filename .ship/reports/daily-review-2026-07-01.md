## Daily review — 2026-07-01

_Snapshot generated 2026-07-01 06:38 UTC. Window: last 24h ending at generation time._

_Sources checked: Ship audit log (`since=2026-06-30T06:33Z`), engine-health, processes/development, inbox, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 (ELS-339 / PR #425 not merged) — report composed manually. Ship dashboard not queried; health fields sourced from engine-health and processes/development instead._

### Ticket movement (24h)

- **ELS-340**: created by `scheduled_routine.ticket_created` @ 2026-07-01T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-01T06:35; **in-flight** — this report)
- **ELS-339**: `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-30T06:33); cycled `dev_implementation` ↔ `qa_manual` ↔ `validation` through 2026-06-30T07:40; `validation` → `code_review` (`ready_next_step` @ 2026-06-30T07:40); then `code_review` **blocked** @ 2026-06-30T07:43 (`transition.validation_failed`, reason `no_approval`)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-30T09:04); inbox item "Daily digest — 2026-06-30" filed @ 2026-06-30T09:02
- **workspace_weekly**: weekly audit inbox item "Weekly audit — 2026-W27" filed @ 2026-06-30T09:02 and again @ 2026-07-01T03:33; bundle finished `ready_next_step` @ 2026-07-01T03:34 with same-time `workflow.coding_leaf.finish_mismatch` (`expected_run_id=run_b01e9d494ba12673`, `actual_run_id=run_ae3e8b1eb22d4ee7`)
- **Agent outcomes in window**: 14 `ready_next_step`, 1 `blocked`, 0 `needs_clarification`

### Stuck / attention

- **ELS-339**: `code_review` blocked because the Phase 4 gate rejected `stage_next=auto_merge` with reason `no_approval`; PR #425 is green but still awaiting human review/approval.
- **workspace_weekly**: recurring `workflow.coding_leaf.finish_mismatch` @ 2026-07-01T03:34 even though the weekly audit inbox item was filed; prior weekly finish also completed @ 2026-06-30T09:03.
- Development process health: **degraded** (25 blocked projection items — stale carryover; 1 fresh `outcome=blocked` finish for ELS-339 in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

No red CI found in the open PR queue. Nine open daily-review PRs are awaiting human review; all checks green as of the 06:38 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~1d | awaiting review | **green** (10/10 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~4d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~5d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~6d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~7d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~8d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~11d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~12d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~13d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and approve **PR #425** (ELS-339) to clear the active `code_review` `no_approval` blocker.
2. Inspect the **Weekly audit — 2026-W27** inbox report and the `workspace_weekly` finish mismatch @ 2026-07-01T03:34 to confirm whether the expected workflow run result was lost or only mis-correlated.
3. Batch-review the stale daily-review PR queue (**#417–#424**) — all green, oldest is 13 days awaiting operator review.
