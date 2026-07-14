## Daily review — 2026-07-14

_Snapshot generated 2026-07-14 06:45 UTC. Window: last 24h ending at generation time (`2026-07-13T06:44:42Z` → `2026-07-14T06:45:00Z`)._

### Ticket movement (24h)

- **ELS-355**: created by `scheduled_routine.ticket_created` @ 2026-07-14T06:30 (routine `daily`, period `2026-07-14`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-14T06:43; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-13T09:06; `noop:no_ticket`); inbox items "Daily digest — 2026-07-13" filed ×2 @ 2026-07-13T09:03 / 09:05
- **workspace_weekly** / weekly-audit: `enumerate` leaf dispatched @ 2026-07-14T03:30; inbox item "Weekly audit — 2026-W29" filed @ 2026-07-14T03:37; leaf finish `ready_next_step` (`workflow_leaf`) @ 2026-07-14T03:37; follow-on steps `rank` / `audit.test-gaps` / `audit.coupling` / `audit.complexity` dispatched @ 2026-07-14T03:50 — **no finish rows for those steps in this window**
- **0** `pr_merge.tracker_done` events in window (no merges)
- No other ticket stage transitions via agent finish / dispatch cascade in the audit window

### Stuck / attention

- **17 open daily-review PRs** (#417–#436) still awaiting operator review/approval; matching inbox blockers for **ELS-331–ELS-354** (plus older ELS-265/194/295/309/329) at `code_review` — same `no_approval` / human-gate pattern; **no new** `blocked` finishes in this 24h window (newest blocker letter remains ELS-354 @ 2026-07-11)
- **weekly-audit** parallel steps (`rank`, `audit.*`) dispatched ~3h before snapshot with no subsequent `agent_run.finish` in audit-log — check whether they are still running or stalled behind the single active lock
- Inbox (`ownership=all`): **91** new items (**28** blockers, **63** reports); default `ownership=mine` list returns **0** (token filter hides the backlog)
- Fresh reports in window: "Daily digest — 2026-07-13" (×2), "Weekly audit — 2026-W29"
- Stale carryover blocker still open: "Engine stalled: daily-digest:scheduled (expired_not_swept)" from 2026-06-15 (engine is healthy now)
- Development process health: **degraded** (`blocked_count=25` / `task_count=25` — projection mainly from the code_review backlog above)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)

### PRs

**17** open PRs on `ElMundiUA/ship` — **all** daily-review / review-stack reports (no product PRs open). **0** red CI; all green after excluding skipped deploy jobs. All awaiting review / operator approval.

| Stack | Tickets | Span | CI | Review |
|-------|---------|------|----|--------|
| Oldest [#417](https://github.com/ElMundiUA/ship/pull/417) → newest [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-331 … ELS-354 | ~26d → ~3d | **green** | awaiting review |

Representative tips:

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~26d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~3d | awaiting review | **green** (7/7 checks) |

Full open set (all green, awaiting review): #417, #418, #419, #420, #421, #422, #423, #424, #425, #426, #429, #430, #431, #432, #434, #435, #436.

### Next actions

1. Batch-triage the **17 open daily-review PRs** (#417–#436): merge the stack or close stale duplicates, and decide whether markdown-only `.ship/reports/` PRs should get a standing auto-merge / approval waiver.
2. Start with newest tip **PR #436** (ELS-354 daily review for 2026-07-11) — CI green; clear its `blocked` / `code_review` freeze after merge-or-close.
3. Skim inbox report **Weekly audit — 2026-W29** and confirm whether weekly-audit steps `rank` / `audit.*` (dispatched 2026-07-14T03:50) finished outside this window or need a human nudge.
