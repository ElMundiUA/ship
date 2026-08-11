## Daily review — 2026-08-11

_Snapshot generated 2026-08-11 06:51 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-383**: created by `scheduled_routine.ticket_created` @ 2026-08-11T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-08-11T06:47; **in-flight** — this report)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-08-10T09:05); inbox item "Daily digest — 2026-08-10" filed @ 2026-08-10T09:04
- **workspace_weekly**: weekly audit coding leaf dispatched @ 2026-08-11T03:30; finished `ready_next_step` @ 2026-08-11T03:37; inbox item "Weekly audit — 2026-W33" filed @ 2026-08-11T03:36; follow-on steps `rank` + `audit.test-gaps` / `audit.coupling` / `audit.complexity` dispatched @ 2026-08-11T03:45
- **0 PR merges** (`pr_merge.tracker_done`) and **0** `dispatch.no_routine` rows in the audit window (18 audit events total; paginated once, window covered)

### Stuck / attention

- **Weekly audit follow-ons**: `rank` + three `audit.*` steps dispatched @ 2026-08-11T03:45 with **no** subsequent `agent_run.finish` observed through generation time (~3h later) — treat as possible stall / silent completion gap
- Weekly audit inbox item created @ 2026-08-11T03:36 ("Weekly audit — 2026-W33")
- Daily digest inbox item created @ 2026-08-10T09:04 ("Daily digest — 2026-08-10")
- Development process health: **degraded** (25 blocked projection items — stale carryover; **no** fresh `outcome=blocked` finishes in window)
- Engine health: **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `stalled=[]`; last finish/dispatch @ 2026-08-11T06:47)
- Inbox counts API: `actionable_new=177` (`blocker=48`, `report=128`, plus 1 improvement); agent-scoped `/inbox` list returned 0 open items (`counts_by_status.new=0`; 5 resolved / 11 dismissed carryover on that view)
- **ELS-383**: in-flight daily review at `dev_implementation` — not stuck solely for being in progress
- Open PR backlog: **37** unmerged PRs (mostly prior daily-review tickets from 2026-06-18 → 2026-08-08) — see PRs

### PRs

37 open PRs on `ElMundiUA/ship`. All listed checks are green (or green + skipped deploy jobs on #425); none red. Entire queue is awaiting review/merge — primarily unmerged daily-review artefacts.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~3d | awaiting review | **green** (7/7) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~4d | awaiting review | **green** (7/7) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~5d | awaiting review | **green** (7/7) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~42d | awaiting review | **green** (10 success / 2 skipped) |
| … | ELS-331…ELS-379 (+ #424) | ~6d–54d | awaiting review | **green** |

Oldest open: [#417](https://github.com/ElMundiUA/ship/pull/417) (ELS-331, ~54d). No open PR yet for ELS-383 at generation time.

### Next actions

1. Merge the daily-review PR backlog starting with **#456** (ELS-382) — CI green; oldest backlog stretches to **#417** / ELS-331 (~54d).
2. Triage inbox report **Weekly audit — 2026-W33** and check whether weekly `rank` / `audit.*` leaves finished after the 03:45 dispatch (no finishes in audit through 06:51).
3. Let **ELS-383** (this report) complete QA → merge so 2026-08-11 lands on `main`; then chip away at the remaining open daily-review PRs.
