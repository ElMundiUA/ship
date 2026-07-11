## Daily review — 2026-07-11

_Snapshot generated 2026-07-11 06:49 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-354**: created by `scheduled_routine.ticket_created` @ 2026-07-11T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-11T06:46; **in-flight** — this report)
- **ELS-347**: `code_review` → blocked (`outcome=blocked` @ 2026-07-10T06:51; `phase4:rejected:no_approval`; `blocked` label + inbox blocker); PR #435 open
- **ELS-348–ELS-353**: created by weekly-audit child tickets @ 2026-07-11T03:36 (titles: unstick daily-review backlog; throttle weekly-audit; `model_catalog.py` tests; stabilize `mcp.py`; Dependabot/secret-scanning API access; re-harvest knowledge); all landed in **Backlog** with `dispatch.no_routine` (no FSM stage)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-10T09:03; `noop:no_ticket`); inbox item "Daily digest — 2026-07-10" filed
- **workspace_weekly** / weekly-audit: dispatched @ 2026-07-11T03:30; inbox item "Weekly audit — 2026-W28" filed @ 2026-07-11T03:34; leaf finish `ready_next_step` (`workflow_leaf`) @ 2026-07-11T03:37
- **0** `pr_merge.tracker_done` events in window

### Stuck / attention

- **ELS-347**: blocked at `code_review` — Phase 4 gate `no_approval` (PR #435 CI green, awaiting human approval); `blocked` label freezes further agent stages
- **16 daily-review tickets** in Review with `blocked` label (ELS-331–ELS-347 / open PRs #417–#435) — same `code_review` / `no_approval` pattern; newest fresh block in window is ELS-347
- Inbox (`ownership=all`): **84** new items (**27** blockers, **57** reports); default `ownership=mine` list returns **0** (token filter hides the backlog)
- Distinct inbox blockers include ELS-331–ELS-347 (+ older ELS-265/194/295/309/329) at `code_review`, plus one stale "Engine stalled: daily-digest" letter from 2026-06-15
- Fresh reports in window: "Weekly audit — 2026-W28" (2026-07-11), "Daily digest — 2026-07-10"
- Development process health: **degraded** (`blocked_count=25` / `task_count=25` — mostly stale inbox-sourced projection; only 1 fresh `outcome=blocked` finish in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- **ELS-348–ELS-353**: Backlog coverage tickets from today's weekly audit — not on SDLC yet (`dispatch.no_routine`)

### PRs

16 open PRs on `ElMundiUA/ship` (all daily-review reports). Oldest #417 (~23d); newest #435 (~24h). **0** red CI; all green after excluding skipped deploy jobs. All awaiting review / operator approval.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~23d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~22d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~21d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~18d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~17d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~16d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~15d | awaiting review | **green** (7/7 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~14d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~11d | awaiting review | **green** (10/10 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~10d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~9d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~8d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~7d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~4d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~3d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~24h | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #435** (ELS-347 daily review for 2026-07-10) — CI green; clear the `blocked` label so validation can advance.
2. Batch-triage the **16 open daily-review PRs** (#417–#435) stuck on the same `no_approval` gate (or decide a standing auto-merge rule for `.ship/reports/` markdown-only PRs).
3. Skim **Weekly audit — 2026-W28** and prioritize Backlog coverage tickets **ELS-348–ELS-353** (especially unstick backlog + throttle weekly-audit).
