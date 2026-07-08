## Daily review — 2026-07-08

_Snapshot generated 2026-07-08 06:51 UTC. Window: last 24h ending at generation time (`since=2026-07-07T06:50:41Z`)._

_Sources checked: Ship audit log (`since=2026-07-07T06:50:41Z`, `limit=200`, 27 rows in single page — no cursor pagination needed), processes/development, inbox, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 and `GET /v1/workspaces/{ws}/dashboard` returned 404 — report composed manually._

### Ticket movement (24h)

- **ELS-345**: created by `scheduled_routine.ticket_created` @ 2026-07-08T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-08T06:49); `dev_implementation` dispatched @ 2026-07-08T06:49 (**in-flight** — this report)
- **ELS-344**: `transition.validation_failed` @ 2026-07-07T06:53 (`reason=no_approval`, `fsm_stage=code_review`); `agent_run.finish` `outcome=blocked` @ 2026-07-07T06:53 (`phase4:rejected:no_approval`); `dispatch.no_routine` cascade gap @ 2026-07-07T06:53
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-07T09:02); inbox item "Daily digest — 2026-07-07" filed @ 2026-07-07T09:02
- **workspace_weekly**: weekly audit dispatched @ 2026-07-07T09:00; finished `ready_next_step` @ 2026-07-07T09:04 (`noop:no_ticket`); inbox item "Weekly audit — 2026-W28" filed @ 2026-07-07T09:03
- **Weekly audit — 2026-W28** (recovery run): `workflow.coding_leaf.dispatched` @ 2026-07-08T03:30; workflow steps dispatched @ 2026-07-08T03:45; finished `ready_next_step` @ 2026-07-08T03:35; inbox item "Weekly audit — 2026-W28" filed @ 2026-07-08T03:34
- **PR merge**: [#433](https://github.com/ElMundiUA/ship/pull/433) merged @ 2026-07-07T15:02 (per-stage execution backend + Cursor model catalog)
- **Agent outcomes in window**: 4 `ready_next_step`, 1 `blocked`, 0 `needs_clarification` (27 audit rows total)

### Stuck / attention

- **ELS-344**: blocked at `code_review` @ 2026-07-07T06:53 (`no_approval` — balanced gate refused auto-merge; inbox blocker filed; `blocked` label applied)
- **14 daily-review PRs** (#417–#432) blocked at `code_review` awaiting human approval — all CI green as of 06:51 UTC read; oldest is ~20 days (#417)
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly stale projection carryover; 1 fresh `outcome=blocked` finish in window on ELS-344)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover). Three "Weekly audit — 2026-W28" report rows exist in the development process overlay (filed 2026-07-07T03:32, 2026-07-07T09:03, 2026-07-08T03:34) — duplicate inbox letters for the same period.

### PRs

No red CI found in the open PR queue. Fourteen open daily-review PRs are awaiting human review; all CI checks green as of the 06:51 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~20d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~19d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~18d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~15d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~14d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~13d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~12d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~11d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~8d | awaiting review | **green** (10/12 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~7d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~6d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~5d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~4d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve **PR #432** (ELS-344) and remove the `blocked` label so validation can retry — same `no_approval` gate that blocked yesterday's report.
2. Batch-review the stale daily-review PR queue (**#417–#431**) — all green CI, oldest is 20 days awaiting operator review.
3. Read the latest **Weekly audit — 2026-W28** inbox report (filed @ 2026-07-08T03:34) and triage the six child tickets filed; decide whether markdown-only daily-review PRs should auto-merge without human review.
