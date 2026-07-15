## Daily review — 2026-07-15

_Snapshot generated 2026-07-15 06:47 UTC. Window: last 24h ending at generation time (`2026-07-14T06:47:00Z` → `2026-07-15T06:47:00Z`). Audit-log: 34 rows, `next_cursor=null` (full window in one page). Dedicated `GET /v1/workspaces/{ws}/daily-review` → **404** — report composed from fallback read APIs listed in Sources._

### Ticket movement (24h)

- **ELS-356**: created by `scheduled_routine.ticket_created` @ 2026-07-15T06:30 (routine `daily`, period `2026-07-15`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-15T06:43; **in-flight** — this report)
- **ELS-355**: `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-14T06:43) → `qa_manual` (`ready_next_step` @ 2026-07-14T06:46) → `validation` → `code_review` (`ready_next_step` @ 2026-07-14T06:50) → **`outcome=blocked`** @ 2026-07-14T06:53 (`phase4:rejected:no_approval`, `tracker:label:blocked`, inbox blocker); subsequent `overlay_frozen_skipped` at `validation` @ 2026-07-14T06:56 (`matched_labels: ["blocked"]`)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-14T09:05; `workspace_daily` → `workspace_daily_done`, `noop:no_ticket`); inbox item "Daily digest — 2026-07-14" filed @ 2026-07-14T09:04
- **workspace_weekly**: weekly digest completed (`ready_next_step` @ 2026-07-14T09:07; `workspace_weekly` → `workspace_weekly_done`, `noop:no_ticket`); inbox item "Weekly audit — 2026-W29" filed @ 2026-07-14T09:07
- **weekly-audit** (workflow leaf, distinct from `workspace_weekly`): `enumerate` leaf dispatched @ 2026-07-15T03:30; leaf finish `ready_next_step` (`workflow_leaf`) @ 2026-07-15T03:33; follow-on steps `rank` / `audit.test-gaps` / `audit.coupling` / `audit.complexity` dispatched @ 2026-07-15T03:45 — **no finish rows for those steps in this window**
- **0** `pr_merge.tracker_done` events in window (no merges)
- No other SDLC ticket refs appeared in agent finishes / ticket creates in this window (only ELS-355 / ELS-356)

### Stuck / attention

- **ELS-355**: fresh in-window `agent_run.finish` with `outcome=blocked` @ 2026-07-14T06:53 (`phase4:rejected:no_approval` at `code_review`); frozen by `blocked` label (`overlay_frozen_skipped` @ 2026-07-14T06:56). **Only** fresh `outcome=blocked` finish in the 24h window.
- Development process health: **degraded** (`blocked_count=25` / `task_count=25`) — projection is mostly stale carryover (inbox-style rows for digests/audits and older code_review gates); do **not** treat 25 as 25 new blocks today
- Engine health: **healthy** (`healthy=true`, `expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`; `last_dispatch_at` / `last_finish_at` 2026-07-15T06:43:46Z)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: `GET /inbox/counts` → **94** open (`all_open=94`; `by_type`: **29** blocker / **65** report; `by_status.new=94`). Default `GET /inbox` list returned **empty** (`items=[]`) under this token/ownership — titles taken only from audit `agent_run.inbox_item` rows: "Daily digest — 2026-07-14", "Weekly audit — 2026-W29"
- **18 open daily-review PRs** (#417–#437) still awaiting operator approval (see PRs); backlog grew vs ~13 (ELS-344 era)

### PRs

**18** open PRs on `ElMundiUA/ship` — all daily-review report PRs. **0** red CI at generation time; review decision empty on all (awaiting approval). Re-queried via `gh pr list` + `statusCheckRollup`.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~27d | awaiting | **green** (7/7) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~26d | awaiting | **green** (7/7) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~25d | awaiting | **green** (7/7) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~22d | awaiting | **green** (7/7) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~21d | awaiting | **green** (7/7) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~20d | awaiting | **green** (7/7) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~19d | awaiting | **green** (7/7) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~18d | awaiting | **green** (7/7) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~15d | awaiting | **green** (12/12) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~14d | awaiting | **green** (7/7) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~13d | awaiting | **green** (7/7) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~12d | awaiting | **green** (7/7) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~11d | awaiting | **green** (7/7) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~8d | awaiting | **green** (7/7) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~7d | awaiting | **green** (7/7) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~5d | awaiting | **green** (7/7) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~4d | awaiting | **green** (7/7) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~24h | awaiting | **green** (7/7) |

### Next actions

1. Batch-approve/merge the **18** green daily-review PRs, starting with newest **[#437](https://github.com/ElMundiUA/ship/pull/437)** (ELS-355 / 2026-07-14), then work oldest→newest or tip-only as preferred.
2. Clear the **`blocked`** label on **ELS-355** (and older tickets past `code_review` stuck the same way) after merge-or-close so `overlay_frozen_skipped` stops freezing validation.
3. Triage inbox report **Weekly audit — 2026-W29** (filed @ 2026-07-14T09:07) and confirm whether weekly-audit steps `rank` / `audit.*` (dispatched 2026-07-15T03:45) finished outside this window or need a nudge.

### Sources

- `GET /v1/workspaces/{ws}/audit-log?limit=200&since=2026-07-14T06:47:00Z` (34 items, `next_cursor=null`; ELS-355 early stage rows also checked with `since=2026-07-14T06:30:00Z`)
- `GET /v1/workspaces/{ws}/engine-health`
- `GET /v1/workspaces/{ws}/processes/development`
- `GET /v1/workspaces/{ws}/inbox/counts` (+ `GET /inbox` list empty under default ownership)
- `GET /v1/workspaces/{ws}/repos`
- `GET /v1/workspaces/{ws}/daily-review` → 404 (not used)
- `gh pr list` / `statusCheckRollup` on `ElMundiUA/ship` (open PRs)
