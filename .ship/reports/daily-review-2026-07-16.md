## Daily review — 2026-07-16

_Snapshot generated 2026-07-16 06:47 UTC. Window: last 24h ending at generation time (`2026-07-15T06:47:00Z` → `2026-07-16T06:47:00Z`). Audit-log (`action=agent`): 23 rows, `next_cursor=null` (full window in one page). Dedicated `GET /v1/workspaces/{ws}/daily-review` → **404** — report composed from fallback read APIs listed in Sources._

### Ticket movement (24h)

- **ELS-357**: created by `scheduled_routine.ticket_created` @ 2026-07-16T06:30 (routine `daily`, period `2026-07-16`; row visible in unfiltered audit-log — not present in `action=agent` slice); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-16T06:40); **in-flight** — dev dispatch @ 2026-07-16T06:40 (this report)
- **ELS-356**: `dev_implementation` → `qa_manual` (`ready_next_step` @ 2026-07-15T06:47) → `validation` → `code_review` (`ready_next_step` @ 2026-07-15T06:57) → **`outcome=blocked`** @ 2026-07-15T06:59 (`phase4:rejected:no_approval`, `tracker:label:blocked`, inbox blocker); `overlay_frozen_skipped` burst @ 2026-07-16T06:38–06:39 across planning / dev / qa / validation / code_review (`matched_labels: ["blocked"]`, reported once)
- **ELS-355**: `overlay_frozen_skipped` @ 2026-07-16T06:39 (`planning`, `matched_labels: ["blocked"]`) — stale carryover from prior-day `code_review` block (no fresh finish in this window)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-15T09:04; `workspace_daily` → `workspace_daily_done`, `noop:no_ticket`); inbox items "Daily digest — 2026-07-15" filed @ 2026-07-15T09:02 and 09:03 (duplicate filings)
- **weekly-audit** (workflow leaf): `enumerate` leaf dispatched @ 2026-07-16T03:30; leaf finish `ready_next_step` (`workflow_leaf`) @ 2026-07-16T03:32; follow-on steps `rank` / `audit.test-gaps` / `audit.coupling` / `audit.complexity` dispatched @ 2026-07-16T03:35 — **no finish rows for those steps in this window**
- **0** `pr_merge.tracker_done` events in window (no merges)
- No other SDLC ticket refs had `agent_run.finish` or `agent_run.dispatch` in this window

### Stuck / attention

- **ELS-356**: fresh in-window `agent_run.finish` with `outcome=blocked` @ 2026-07-15T06:59 (`phase4:rejected:no_approval` at `code_review`, PR [#438](https://github.com/ElMundiUA/ship/pull/438)); frozen by `blocked` label (`overlay_frozen_skipped` burst @ 2026-07-16T06:38–06:39). **Only** fresh `outcome=blocked` finish in the 24h window.
- **ELS-355**: stale `overlay_frozen_skipped` @ 2026-07-16T06:39 (`planning`, `matched_labels: ["blocked"]`) — prior-day block, not a new failure today.
- Development process health: **degraded** (`blocked_count=25` / `task_count=25`) — projection is mostly stale carryover (inbox digests/audits and older `code_review` gates); do **not** treat 25 as 25 new blocks today.
- Engine health: **healthy** (`healthy=true`, `expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`; `last_dispatch_at` / `last_finish_at` 2026-07-16T06:40:58Z)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: `GET /inbox/counts` → **97** open (`all_open=97`; `by_type`: **30** blocker / **67** report; `by_status.new=97`). Default `GET /inbox` list returned **empty** (`items=[]`) under this token/ownership — titles from audit `agent_run.inbox_item` rows: "Daily digest — 2026-07-15" (×2)
- **19 open daily-review PRs** (#417–#438) still awaiting operator approval (see PRs); backlog grew by one vs yesterday (#438 ELS-356)

### PRs

**19** open PRs on `ElMundiUA/ship` — all daily-review report PRs. **0** red CI at generation time; review decision empty on all (awaiting approval). Re-queried via `gh pr list` + `statusCheckRollup`.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~27d | awaiting | **green** (7/7) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~27d | awaiting | **green** (7/7) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~25d | awaiting | **green** (7/7) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~23d | awaiting | **green** (7/7) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~22d | awaiting | **green** (7/7) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~20d | awaiting | **green** (7/7) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~19d | awaiting | **green** (7/7) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~19d | awaiting | **green** (7/7) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~15d | awaiting | **green** (12/12) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~15d | awaiting | **green** (7/7) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~14d | awaiting | **green** (7/7) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~12d | awaiting | **green** (7/7) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~12d | awaiting | **green** (7/7) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~9d | awaiting | **green** (7/7) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~7d | awaiting | **green** (7/7) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~6d | awaiting | **green** (7/7) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~4d | awaiting | **green** (7/7) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~1d | awaiting | **green** (7/7) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | <1d | awaiting | **green** (7/7) |

### Next actions

1. Batch-approve/merge the **19** green daily-review PRs, starting with newest **[#438](https://github.com/ElMundiUA/ship/pull/438)** (ELS-356 / 2026-07-15) and **[#437](https://github.com/ElMundiUA/ship/pull/437)** (ELS-355), then work oldest→newest or tip-only as preferred.
2. Clear the **`blocked`** label on **ELS-356** after merge-or-close so validation stops hitting `overlay_frozen_skipped`; same pattern applies to ELS-355 and older tickets stuck at `code_review`.
3. Triage the **97** unassigned inbox letters (`ownership=unassigned` / `all`; default `mine` hides them) — includes duplicate "Daily digest — 2026-07-15" filings and 30 code_review blocker notices.

### Sources

- `GET /v1/workspaces/{ws}/audit-log?limit=200&since=2026-07-15T06:47:00Z&action=agent` (23 items, `next_cursor=null`)
- `GET /v1/workspaces/{ws}/audit-log?limit=50&since=2026-07-16T06:00:00Z` (unfiltered; `scheduled_routine.ticket_created` for ELS-357 @ 06:30 not in `action=agent` slice)
- `GET /v1/workspaces/{ws}/engine-health`
- `GET /v1/workspaces/{ws}/processes/development`
- `GET /v1/workspaces/{ws}/inbox/counts` (+ `GET /inbox` list empty under default ownership)
- `GET /v1/workspaces/{ws}/repos`
- `GET /v1/workspaces/{ws}/daily-review` → 404 (not used)
- `gh pr list` / `statusCheckRollup` on `ElMundiUA/ship` (open PRs)
