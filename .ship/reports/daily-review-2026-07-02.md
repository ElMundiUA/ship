## Daily review — 2026-07-02

_Snapshot generated 2026-07-02 06:41 UTC. Window: last 24h ending at generation time (`since=2026-07-01T06:34:17Z`)._

_Sources checked: Ship audit log (`since=2026-07-01T06:34:17Z`, `limit=100`, 31 items in single page — no cursor pagination needed), engine-health, processes/development, inbox, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 (ELS-339 / PR #425 not merged) — report composed manually._

### Ticket movement (24h)

- **ELS-341**: created by `scheduled_routine.ticket_created` @ 2026-07-02T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-02T06:39; **in-flight** — this report)
- **ELS-340**: `planning` → `dev_implementation` → `qa_manual` → `validation` (`ready_next_step` ×3 @ 2026-07-01T06:35–06:44); `validation` → `code_review` **blocked** @ 2026-07-01T06:46 (`transition.validation_failed`, reason `no_approval`); `overlay_frozen_skipped` at `validation` @ 2026-07-01T06:58 (`blocked` label)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-01T09:03); inbox item "Daily digest — 2026-07-01" filed @ 2026-07-01T09:02
- **workspace_weekly**: weekly audit inbox item "Weekly audit — 2026-W27" filed @ 2026-07-02T03:36; **in-flight** — no `agent_run.finish` for `workspace_weekly` in window after dispatch
- **Agent outcomes in window**: 5 `ready_next_step`, 1 `blocked`, 0 `needs_clarification`

### Stuck / attention

- **ELS-340**: `code_review` blocked because the Phase 4 gate rejected `stage_next=auto_merge` with reason `no_approval`; `overlay_frozen_skipped` at `validation` @ 2026-07-01T06:58 froze the pipeline despite dev/QA completing. PR #426 is green but still awaiting human review/approval.
- **workspace_weekly**: inbox item "Weekly audit — 2026-W27" filed @ 2026-07-02T03:36 with no matching `workspace_weekly` finish in the 24h window — treat as in-flight.
- Development process health: **degraded** (25 blocked projection items — stale carryover; 1 fresh `outcome=blocked` finish for ELS-340 in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

No red CI found in the open PR queue. Ten open daily-review PRs are awaiting human review; all CI checks green as of the 06:41 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~1d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~1d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~4d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~5d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~6d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~8d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~9d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~11d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~13d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~13d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and approve **PR #426** (ELS-340) to clear the active `code_review` `no_approval` blocker and unfreeze validation.
2. Inspect the **Weekly audit — 2026-W27** inbox report filed @ 2026-07-02T03:36 — no `workspace_weekly` finish in window; confirm whether the weekly routine is still running or stalled.
3. Batch-review the stale daily-review PR queue (**#417–#425**) — all green, oldest is 13 days awaiting operator review.
