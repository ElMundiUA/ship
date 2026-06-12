## Daily review — 2026-06-11

_Snapshot generated 2026-06-11 10:11 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-203**: decomposition→planning_done (`ready_next_step`)
- **ELS-216**: planning→dev_implementation → dev_implementation→qa_manual (`ready_next_step, ready_next_step`)
- **ELS-263**: planning→dev_implementation (`ready_next_step`)
- **ELS-263**: created by `scheduled_routine.ticket_created` (routine `daily`) @ 2026-06-11T09:56:41Z
- **daily-digest**: workspace bundle ran @ 2026-06-11T09:00:00Z (`workspace_daily` routine)
- **24 tickets** moved Backlog → Done via tracker (`tracker.event.received` + `dispatch.no_routine`, no agent finish): ELS-63, ELS-217..ELS-234, ELS-241..ELS-245

### Stuck / attention

- **ELS-27** (In Progress, no PR): ELS-27: Linear adapter: richer label namespaces (`ready:*`, `needs:clarification`)
- **ELS-15** (Backlog, no PR): ELS-15: Record raw walk-through video for closed-beta blog (E03 T10)
- **ELS-192** (Review, PR #355): ELS-192: Anchor: UI/UX Issues on Mobile
- FSM flow counts (non-zero): `planning`=2, `dev_implementation`=1
- Bundle drift on `ElMundiUA/ship`: installed `0.38` vs current `0.40`
- `runs_in_flight`: 227 (counts all non-terminal workflow rows, not unique tickets)
- Inbox: 0 new items
- No `blocked` or `needs_clarification` finishes in window

### PRs

- **#365** / ELS-217 (today, recent): **red** (`check` failed)
- **#364** / ELS-216 (today, recent): **red** (`playwright (landing smoke)` failed)
- **#360** (2d, recent): green
- **#358** / ELS-202 (6d, awaiting review): green
- **#357** (6d, awaiting review): green
- **#356** / ELS-194 (6d, awaiting review): green
- **#355** / ELS-192 (6d, awaiting review): green
- **#354** (9d, awaiting review): green
- **#329** (15d, awaiting review): green
- _(Note: PR(s) #355 in ops WIP but outside dashboard recent-10 cache — listed above via GitHub.)_

### Next actions

1. Fix red CI on PR #365 (`check` / bundle-version-check) before merging the headless-pivot batch.
2. Re-run or fix failing `ci` on PR #364 (ELS-216 — `playwright (landing smoke)`) so validation can proceed.
3. Merge or close stale open PRs #356–#358, #360, #354, and #329 (>3 days, green CI, awaiting review).
