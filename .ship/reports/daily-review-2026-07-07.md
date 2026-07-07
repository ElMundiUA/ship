## Daily review — 2026-07-07

_Snapshot generated 2026-07-07 06:39 UTC. Window: last 24h ending at generation time (`since=2026-07-06T06:39:00Z`)._

_Sources checked: Ship audit log (`since=2026-07-06T06:39:00Z`, `limit=200`, 18 rows in single page — no cursor pagination needed), engine-health, processes/development, inbox, repos; GitHub open PR/check reads for `ElMundiUA/ship`. `GET /v1/workspaces/{ws}/daily-review` returned 404 and `GET /v1/workspaces/{ws}/dashboard` returned 404 — report composed manually._

### Ticket movement (24h)

- **ELS-344**: created by `scheduled_routine.ticket_created` @ 2026-07-07T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-07T06:37); `dev_implementation` dispatched @ 2026-07-07T06:37 (**in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-06T09:05); inbox item "Daily digest — 2026-07-06" filed @ 2026-07-06T09:04
- **Weekly audit — 2026-W28**: `workflow.coding_leaf.dispatched` @ 2026-07-07T03:30; inbox item "Weekly audit — 2026-W28" filed @ 2026-07-07T03:33; finished `ready_next_step` @ 2026-07-07T03:33 (complete — unlike W27 orphan pattern)
- **Agent outcomes in window**: 3 `ready_next_step`, 0 `blocked`, 0 `needs_clarification`
- **Quiet day** for SDLC ticket work beyond ELS-344 and routine dispatches (18 audit rows total; no PR merges in window)

### Stuck / attention

- **ELS-344**: `dev_implementation` in progress for this report; do not mark Done until operator merges the PR.
- **13 daily-review PRs** (#417–#431) blocked at `code_review` awaiting human approval — all CI green as of 06:39 UTC read; oldest is 18 days.
- Development process health: **degraded** (25 blocked projection items — mostly stale carryover; 0 fresh `outcome=blocked` finishes in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

No red CI found in the open daily-review PR queue. Thirteen open daily-review PRs are awaiting human review; all CI checks green as of the 06:39 UTC GitHub read.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~18d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~18d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~16d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~14d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~12d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~11d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~10d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~9d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~6d | awaiting review | **green** (10/12 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~5d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~4d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~3d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~2d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Read the **Weekly audit — 2026-W28** inbox report filed @ 2026-07-07T03:33 — W28 finished cleanly (`ready_next_step`); review child tickets filed.
2. Batch-review the stale daily-review PR queue (**#417–#431**) — all green CI, oldest is 18 days awaiting operator review.
3. Decide whether markdown-only daily-review PRs should auto-merge without human review — 13 PRs are piling up at `code_review`.
