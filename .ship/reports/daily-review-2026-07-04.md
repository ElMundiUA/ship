## Daily review — 2026-07-04

_Snapshot generated 2026-07-04 06:43 UTC. Window: last 24h ending at generation time (`since=2026-07-03T06:43:05Z`)._

_Sources checked: Ship audit log (`since=2026-07-03T06:43:05Z`, `limit=200`, 29 rows in single page — no cursor pagination needed), engine-health, processes/development, inbox, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 (ELS-339 / PR #425 not merged) and `GET /v1/workspaces/{ws}/dashboard` returned 404 — report composed manually._

### Ticket movement (24h)

- **ELS-343**: created by `scheduled_routine.ticket_created` @ 2026-07-04T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-04T06:41); `dev_implementation` dispatched @ 2026-07-04T06:41 (**in-flight** — this report)
- **ELS-342**: `planning` → `dev_implementation` → `qa_manual` → `validation` → `code_review` (`ready_next_step` ×4 @ 2026-07-03T06:45–06:54); **blocked** at `code_review` @ 2026-07-03T06:57 (`transition.validation_failed`, reason `no_approval`)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-03T09:03); inbox item "Daily digest — 2026-07-03" filed @ 2026-07-03T09:03
- **Weekly audit — 2026-W27**: `workflow.coding_leaf.dispatched` @ 2026-07-04T03:30; inbox item "Weekly audit — 2026-W27" filed @ 2026-07-04T03:33 (no `workspace_weekly` `agent_run.finish` in window)
- **Agent outcomes in window**: 5 `ready_next_step`, 1 `blocked`, 0 `needs_clarification`

### Stuck / attention

- **ELS-342**: `code_review` blocked because the Phase 4 gate rejected `stage_next=auto_merge` with reason `no_approval` @ 2026-07-03T06:57. PR #430 is green but still awaiting human review/approval.
- **ELS-341**: still blocked at `code_review` from prior window (`no_approval`); PR #429 green, awaiting review (carryover — no fresh finish in this window).
- **Weekly audit — 2026-W27**: inbox item filed @ 2026-07-04T03:33 with no matching `workspace_weekly` finish in the 24h window — treat as in-flight.
- **ELS-343**: dev_implementation in progress for this report; do not mark Done until operator merges the PR.
- Development process health: **degraded** (25 blocked projection items — mostly stale carryover; 1 fresh `outcome=blocked` finish for ELS-342 in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

No red CI found in the open PR queue. Twelve open daily-review PRs are awaiting human review; all CI checks green as of the 06:43 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~15d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~15d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~13d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~11d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~10d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~8d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~7d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~7d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~3d | awaiting review | **green** (10/12 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~3d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~2d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~23h | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review and approve **PR #430** (ELS-342) to clear the fresh `code_review` `no_approval` blocker from this window.
2. Read the **Weekly audit — 2026-W27** inbox report filed @ 2026-07-04T03:33 — no `workspace_weekly` finish in window; confirm whether child tickets were created.
3. Batch-review the stale daily-review PR queue (**#417–#426**) — all green, oldest is 15 days awaiting operator review.
