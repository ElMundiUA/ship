## Daily review — 2026-06-26

_Snapshot generated 2026-06-26 06:46 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-336**: PR [#422](https://github.com/ElMundiUA/ship/pull/422) opened from `cursor/ship-developer-ELS-336` @ 2026-06-25T06:48; CI completed green @ 2026-06-25T06:58; `code_review` **blocked** (`no_approval`) @ 2026-06-25T06:59.
- **workspace_daily**: daily digest filed @ 2026-06-25T09:03; summary called out ELS-336's merge gate block, the weekly audit run-id mismatch, and the six open daily-review PRs.
- **ELS-337**: created for the 2026-06-26 daily review and moved `planning` → `dev_implementation` @ 2026-06-26T06:44; **in-flight** — this report.
- **0 PR merges** observed in the review window.

### Stuck / attention

- **ELS-331**: `code_review` **blocked** since 2026-06-18T06:56 (`no_approval`; PR #417 open).
- **ELS-332**: `code_review` **blocked** since 2026-06-19T06:48 (`no_approval`; PR #418 open).
- **ELS-333**: `code_review` **blocked** since 2026-06-20T06:56 (`no_approval`; PR #419 open).
- **ELS-334**: `code_review` **blocked** since 2026-06-23T06:46 (`no_approval`; PR #420 open).
- **ELS-335**: `code_review` **blocked** since 2026-06-24T06:50 (`no_approval`; PR #421 open).
- **ELS-336**: `code_review` **blocked** since 2026-06-25T06:59 (`no_approval`; PR #422 open — fresh in this window).
- `workflow.coding_leaf.finish_mismatch` remains the top non-PR ops issue from the W26 weekly audit (expected `run_631473d36c0d89d0`, actual `run_beb767e21e8f35e5`).
- Development process health: **degraded** (`blocked_count=25`; Planning projection shows 43 blocked items, mostly inbox/report carryover).
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, no stalled locks).
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`).
- Data limitation: Ship dashboard PR/cache endpoints returned 404, so PR review/CI rows below use the verified read-only PR/check listing rather than the dashboard cache.

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~8d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~7d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~6d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~3d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~2d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~1d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Review the daily-review PR queue **oldest first**: **#417** (ELS-331), then **#418**, **#419**, **#420**, **#421**, **#422** — all are open, green, and waiting on human approval.
2. Triage the W26 weekly audit `workspace_weekly` `finish_mismatch` so routine run IDs correlate cleanly before another weekly report fires.
3. Let **ELS-337** (this report) complete dev → QA so today's snapshot joins the same review queue with a current status baseline.
