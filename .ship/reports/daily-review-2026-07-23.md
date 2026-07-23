## Daily review — 2026-07-23

_Snapshot generated 2026-07-23 06:39 UTC. Window: last 24h ending at generation time. Audit-log first page covered the full window (43 events; oldest fetched entry aged out past cutoff)._

### Ticket movement (24h)

- **ELS-365**: created by `scheduled_routine.ticket_created` @ 2026-07-23T06:30 (routine `daily`, period `2026-07-23`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-23T06:35); developer dispatched @ 2026-07-23T06:35 (**in-flight** — this report)
- **ELS-363**: `planning` → `dev_implementation` → `qa_manual` → `code_review` (`ready_next_step` ×3 @ 2026-07-22T06:48–07:03); then `outcome=blocked` at `code_review` (`phase4:rejected:no_approval`, `tracker:label:blocked` @ 2026-07-22T07:06); `overlay_frozen_skipped` at `validation` @ 2026-07-22T07:10 (`matched_labels: blocked`)
- **ELS-364**: `agent_run.ticket_created` then `dispatch.no_routine` @ 2026-07-23T03:41 (tracker poll; Backlog + `needs:intake` — not an SDLC stage move)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-07-22T09:03); inbox item "Daily digest — 2026-07-22" filed @ 2026-07-22T09:03
- **workspace_weekly** / **weekly-audit**: leaf `enumerate` dispatched @ 2026-07-23T03:30, finished `ready_next_step` @ 2026-07-23T03:38; follow-on steps (`rank`, `audit.test-gaps`, `audit.coupling`, `audit.complexity`) dispatched @ 2026-07-23T03:40 — **no `agent_run.finish` for those run ids observed afterward in this window**; inbox item "Weekly audit — 2026-W30" filed @ 2026-07-23T03:37
- **No PR merges** (`pr_merge.tracker_done`) in the window

### Stuck / attention

- **ELS-363** (Daily review — 2026-07-22): frozen at validation/`blocked` — Phase 4 `no_approval` at code_review → auto_merge; `overlay_frozen_skipped` @ 2026-07-22T07:10; PR [#443](https://github.com/ElMundiUA/ship/pull/443) CI green, awaiting human approval (do not clear or merge from this ticket)
- **24 open daily-review tickets** on orphan list with `blocked` + Review state (ELS-331…ELS-363 stack) — same `no_approval` freeze pattern; carryover, not new 24h failures
- **ELS-346** (Daily review — 2026-07-09): still **Backlog** with only `stage:planning` (no open PR in the `#417`…`#443` stack)
- Weekly-audit follow-on steps after `enumerate` (dispatched 03:40) lack finish events in the audit window — glance if still hung after this report lands
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`; last dispatch/finish @ 2026-07-23T06:35)
- Bundle drift: **none** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox counts: `actionable_new=116` (`blocker=35`, `report=81`; `by_status.new=116`; 150 resolved / 202 dismissed carryover). **Note:** `GET …/inbox?status=new` returned 0 items for this run token while `/inbox/counts` still shows 116 — list visibility gap; rely on counts + audit `agent_run.inbox_item` for fresh reports
- Fresh inbox reports in window (from audit): "Daily digest — 2026-07-22", "Weekly audit — 2026-W30"
- No `needs:clarification` labels on orphan tickets; no `finish_mismatch` in the 24h audit window
- Orphans also include Backlog product tickets **ELS-322** (Bug), **ELS-319** / **ELS-318** (Feature) — not FSM-stuck; not actioned here
- **ELS-365** is this report run (In Progress) — not stuck

### PRs

**24 open PRs** on `ElMundiUA/ship` — all daily-review artifacts (`#417`…`#443`, with gaps for merged/closed numbers). Summarized rather than tabulated:

| Slice | PR | Ticket (from title) | Age (approx) | Review | CI |
|-------|----|---------------------|--------------|--------|-----|
| Oldest | [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~35d | awaiting review | **green** (7/7) |
| Mid | [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~20d | awaiting review | **green** (7/7) |
| Newest | [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~1d | awaiting review | **green** (7/7) |

- Stack CI: **24 green / 0 red / 0 pending**; every PR has empty `reviewDecision` (awaiting human approval)
- No non-daily-review open PRs at generation time
- **ELS-365** has no open PR yet (this report’s PR lands after this commit)

### Next actions

1. Decide **merge vs close** for the **24-PR** daily-review backlog (`#417`…`#443`) stuck on `no_approval` / `blocked` — start with **[#443](https://github.com/ElMundiUA/ship/pull/443)** (ELS-363, yesterday) or batch-close stale duplicates; human call only.
2. Triage inbox **Weekly audit — 2026-W30** and chip down `actionable_new=116` (start with the **35 blockers** ahead of the 81 reports); confirm weekly-audit follow-on steps after 03:40 finished or retry if stalled.
3. Let **ELS-365** (this report) finish QA → review; do not auto-merge from the agent.
