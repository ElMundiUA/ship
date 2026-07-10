## Daily review — 2026-07-10

_Snapshot generated 2026-07-10 06:38 UTC. Window: last 24h ending at generation time (`since=2026-07-09T06:38:00Z`)._

_Sources checked: Ship audit log (`since=2026-07-09T06:38:00Z`, `limit=200`, 12 rows in window — single page, no cursor pagination needed), processes/development, inbox/counts, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 and `GET /v1/workspaces/{ws}/dashboard` returned 404 — report composed manually._

### Ticket movement (24h)

_Quiet 24h window — 12 audit rows; no `pr_merge.tracker_done` events._

- **ELS-347**: created by `scheduled_routine.ticket_created` @ 2026-07-10T06:30 (routine `daily`); `planning` dispatched @ 2026-07-10T06:33; `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-10T06:37); `dev_implementation` dispatched @ 2026-07-10T06:37 (**in-flight** — this report)
- **ELS-346**: created by `scheduled_routine.ticket_created` @ 2026-07-09T06:30 (routine `daily`); `planning` dispatched @ 2026-07-09T06:31; `dispatch.lock_released` with `conclusion=failure` @ 2026-07-09T06:46 (workflow run failed — ticket lock cleared)
- **workspace_daily**: daily digest dispatched @ 2026-07-09T09:00; completed (`ready_next_step` @ 2026-07-09T09:09); inbox item "Daily digest — 2026-07-09" filed @ 2026-07-09T09:08
- **workspace_weekly**: `workflow.coding_leaf.dispatched` (`weekly-audit`) @ 2026-07-10T03:30; inbox item "Weekly audit — 2026-W28" filed @ 2026-07-10T03:34
- **Agent outcomes in window**: 2 `ready_next_step`, 0 `blocked`, 0 `needs_clarification` (12 audit rows total)

### Stuck / attention

- **ELS-344** / **ELS-345**: overlay-blocked at `code_review` awaiting human approval — PRs [#432](https://github.com/ElMundiUA/ship/pull/432) and [#434](https://github.com/ElMundiUA/ship/pull/434) open, all CI green
- **15 daily-review PRs** (#417–#434, excluding merged #427/#428) blocked at `code_review` awaiting operator review — all CI green as of 06:38 UTC read; oldest is ~21 days (#417)
- **ELS-346**: yesterday's daily ticket stalled — `agent_run.dispatch` @ 2026-07-09T06:31 followed by `dispatch.lock_released` `conclusion=failure` @ 2026-07-09T06:46; needs operator triage
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — stale projection carryover; no fresh `outcome=blocked` agent finishes in window)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: **81 actionable new** (`actionable_new=81`, `reports_new=55`, `blocker=26`); 149 resolved, 200 dismissed carryover. Five duplicate "Weekly audit — 2026-W28" inbox letters filed (2026-07-07T03:32, 2026-07-07T09:03, 2026-07-08T03:34, 2026-07-09T03:35, 2026-07-10T03:34) — same period, not deduped.

### PRs

No red CI found in the open PR queue. Fifteen open daily-review PRs are awaiting human review; all CI checks green as of the 06:38 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~21d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~20d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~19d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~16d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~15d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~14d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~13d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~12d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~9d | awaiting review | **green** (10/12 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~8d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~7d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~6d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~5d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~2d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Batch-approve the stale daily-review PR queue starting with **#417** (ELS-331, ~21 days) through **#434** (ELS-345) — all green CI, blocked only by human `code_review` gate.
2. Triage **ELS-346** — yesterday's daily ticket failed its workflow run (`dispatch.lock_released` `conclusion=failure` @ 2026-07-09T06:46); re-dispatch or close manually.
3. Read the latest **Weekly audit — 2026-W28** inbox report (filed @ 2026-07-10T03:34) and dismiss or resolve the four older duplicate W28 letters clogging the 81-item inbox backlog.
