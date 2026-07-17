## Daily review — 2026-07-17

_Snapshot generated 2026-07-17 06:44 UTC. Window: last 24h ending at generation time (2026-07-16 06:44 → 2026-07-17 06:44 UTC). All figures sourced from Ship's run-context API surface (audit-log, processes, inbox projections) + GitHub PR/CI status for `ElMundiUA/ship`._

### Ticket movement (24h)

- **ELS-358** (`daily:2026-07-17`, "Daily review — 2026-07-17"): created by `scheduled_routine.ticket_created` @ 2026-07-17T06:30; `planning` → `dev_implementation` (`ready_next_step` @ 2026-07-17T06:38; **in-flight** — this report)
- **ELS-357** ("Daily review — 2026-07-16"): `planning` → `dev_implementation` → `qa_manual` → `code_review` (`ready_next_step` ×3 @ 06:40–06:51); then `blocked` at `code_review` (`no_approval`) @ 2026-07-16T06:52 with `transition.validation_failed` (`code_review` → `auto_merge`, reason `no_approval`); re-dispatched to `validation` (tracker_poll) @ 06:54 → `overlay_frozen_skipped` @ 06:55 (frozen)
- **ELS-356** ("Daily review — 2026-07-15"): no movement — `overlay_frozen_skipped` across `planning`/`dev_implementation`/`qa_manual`/`code_review`/`validation` @ 2026-07-16T06:39 (blocked label)
- **ELS-355** ("Daily review — 2026-07-14"): no movement — `overlay_frozen_skipped` at `planning` @ 2026-07-16T06:39 (blocked label)
- **daily-digest** (`workspace_daily`): dispatched @ 2026-07-16T09:00 (`daily_tick`); `ready_next_step` (`workspace_daily` → `workspace_daily_done`) @ 09:06; inbox item "Daily digest — 2026-07-16" filed @ 09:05
- **weekly-audit** (`workspace_weekly`): coding-leaf dispatched @ 2026-07-17T03:30; `enumerate` → `ready_next_step` @ 03:40; fan-out steps `rank` + `audit.test-gaps` + `audit.coupling` + `audit.complexity` dispatched @ 03:45 (in-flight, no terminal finish in window)
- **1 ticket** via `dispatch.no_routine` (tracker poll, no agent finish): ELS-357 @ 06:52
- **0 PR merges** (`pr_merge.tracker_done`) in window

### Stuck / attention

- **ELS-357** (PR [#439](https://github.com/ElMundiUA/ship/pull/439)): `blocked` at `code_review` @ 2026-07-16T06:52 (`no_approval`) → `validation_failed` → now `overlay_frozen_skipped` at `validation`. Blocked label froze the pipeline despite CI **green** (7/7) — needs operator approval/merge to advance.
- **ELS-356** (PR [#438](https://github.com/ElMundiUA/ship/pull/438)) & **ELS-355** (PR [#437](https://github.com/ElMundiUA/ship/pull/437)): no movement in 24h; `overlay_frozen_skipped` (blocked label). Frozen daily-review reports, CI green, awaiting review.
- **Development process health: degraded** — 25/25 projection tasks flagged `blocked` (mostly stale carryover from the frozen daily-review PR backlog; only one fresh `outcome=blocked` in window: ELS-357 @ `code_review`).
- **Decomposition process: ok** (2 states, 0 blocked).
- Notification emitted: "ELS-357 blocked at code_review" @ 2026-07-16T06:52.
- Inbox: **0 new** (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover; "Daily digest — 2026-07-16" filed then cleared).
- Adapter diagnostics: GitHub Actions runner **ok**; tracker & default-agent adapters report **unknown** (FSM mapping / explicit AgentProfile not yet configured — this view uses the Ship-managed projection).
- **Gap (unavailable this run):** the engine lock projection (`expired_unswept_locks`/`active_locks`) and bundle-version-drift endpoints returned HTTP 404 on the run-context surface, so those figures from prior reports are **not** reported here (not fabricated). Audit-log lock events in-window are balanced (`dispatch.lock_held`=1, `dispatch.lock_released`=2) — no stuck locks observed.

### PRs

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~1d | awaiting review | **green** (7/7) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~2d | awaiting review | **green** (7/7) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~2d | awaiting review | **green** (7/7) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~5d | awaiting review | **green** (7/7) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~6d | awaiting review | **green** (7/7) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~8d | awaiting review | **green** (7/7) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~9d | awaiting review | **green** (7/7) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~12d | awaiting review | **green** (7/7) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~13d | awaiting review | **green** (7/7) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~14d | awaiting review | **green** (7/7) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~15d | awaiting review | **green** (7/7) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~16d | awaiting review | **green** (10/10; 2 deploy steps skipped) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~19d | awaiting review | **green** (7/7) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~20d | awaiting review | **green** (7/7) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~21d | awaiting review | **green** (7/7) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~22d | awaiting review | **green** (7/7) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~23d | awaiting review | **green** (7/7) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~26d | awaiting review | **green** (7/7) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~28d | awaiting review | **green** (7/7) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~28d | awaiting review | **green** (7/7) |

_20 open PRs, all CI green (no red checks), all awaiting review. The oldest 15 are >6d old — an unreviewed daily-review backlog._

### Next actions

1. **Drain the PR backlog**: review + merge the green daily-review PRs (all 20 CI-green, awaiting review; the oldest 15 are #417–#436 at 5–28d). The unreviewed backlog is the root cause of the frozen-pipeline cascade below.
2. **Unblock ELS-357** (PR #439): approve/merge or clear its blocked label — it's frozen at `validation` after `no_approval` at `code_review` despite 7/7 green CI; ELS-355 (#437) and ELS-356 (#438) are frozen the same way.
3. **Confirm the Development-process degraded flag drops** after 1–2: the 25/25 `blocked` projection is largely stale carryover from that frozen backlog, not fresh failures (only ELS-357 hit a fresh `outcome=blocked` in window).
