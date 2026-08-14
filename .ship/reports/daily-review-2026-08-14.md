## Daily review — 2026-08-14

_Snapshot generated 2026-08-14 06:41 UTC. Window: last 24h ending at generation time (`since=2026-08-13T06:39:35Z`; unfiltered `GET /audit-log` returned 44 rows, no further pages)._

### Ticket movement (24h)

- **ELS-385** (Daily review — 2026-08-13): `planning` → `dev_implementation` → `qa_manual`/`validation` → `code_review` (`ready_next_step` finishes); `transition.validation_failed` (`reason=no_approval` at `code_review`, stage_next `auto_merge`) → `outcome=blocked` + `notify.emit` (“ELS-385 blocked at code_review”); later re-dispatch to `validation` hit `agent_run.overlay_frozen_skipped` @ 2026-08-13T07:43Z
- **ELS-386** (Daily review — 2026-08-14): created by `scheduled_routine.ticket_created` (`daily:2026-08-14` @ 2026-08-14T06:30Z); `planning` → `dev_implementation` (`ready_next_step` @ 2026-08-14T06:38Z; **in-flight** — this report)
- **workspace_daily** (`daily-digest`): dispatched @ 2026-08-13T09:00Z; filed inbox “Daily digest — 2026-08-13”; finished `ready_next_step` (`workspace_daily` → `workspace_daily_done`); also filed a short-lived “test” inbox item that was `inbox.disposition.dismiss`’d
- **workspace_weekly** (`weekly-audit`): `workflow.coding_leaf.dispatched` @ 2026-08-14T03:30Z; inbox “Weekly audit — 2026-W33” filed @ 2026-08-14T03:34Z; finish `ready_next_step` @ 2026-08-14T03:36Z; follow-on audit leaf steps dispatched @ 03:45Z
- **0** `pr_merge.tracker_done` events in window (no merges)

### Stuck / attention

- **ELS-385**: labels `stage:planning`, `stage:dev_implementation`, `stage:validation`, `blocked`; Linear state `Review`; fresh-window `outcome=blocked` at `code_review` (`no_approval`) plus `overlay_frozen_skipped` at `validation` — not stale noise
- Development process projection: **degraded** (`task_count=25`, `blocked_count=25`). Treat as mostly **carryover** (inbox/digest/blocker residue in `tasks[]`); the only fresh `outcome=blocked` / `overlay_frozen_skipped` pair in the 24h audit window is ELS-385
- Orphan-tickets admin list: **45** tickets with FSM labels / projection gaps (includes ELS-386 in-flight and a long stack of prior daily reviews carrying `blocked`, e.g. ELS-385…ELS-383)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Tracker: Linear **connected**; `autonomy_paused=false`
- Inbox (`GET /inbox/counts`, `ownership` pile): **189** `new` (`by_type`: report 136, blocker 52, improvement 1; also dismissed 204 / resolved 151). Do not enumerate; freshest audit-window filings to triage first: “Weekly audit — 2026-W33”, “Daily digest — 2026-08-13”, “ELS-385 blocked at code_review”

### PRs

Open on `ElMundiUA/ship` via `gh pr list`: **40** PRs, all daily-review (or related) branches. **No red CI** in the rollup sample; **all** have empty `reviewDecision` (awaiting review). No duplicate open PRs for the same ticket.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~24h | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~2d | awaiting review | **green** (7/7) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~3d | awaiting review | **green** (7/7) |
| … | ELS-382…ELS-331 | older | awaiting review | **green** (37 further open daily-review PRs) |

### Next actions

1. Review and merge **PR #459** (ELS-385 daily review for 2026-08-13) — CI green; clear the ticket’s `blocked` overlay so `code_review` / auto-merge is not frozen again.
2. Triage inbox report **Weekly audit — 2026-W33** (filed 2026-08-14T03:34Z) before older June-era letters.
3. Triage inbox report **Daily digest — 2026-08-13** (filed 2026-08-13T09:05Z); leave the 40-PR backlog for a separate merge pass after #459.
