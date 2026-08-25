## Daily review — 2026-08-25

_Snapshot generated 2026-08-25 06:47 UTC. Window: last 24h ending at generation time (2026-08-24 06:47:38 UTC → 2026-08-25 06:47:38 UTC)._

Sources: `GET /audit-log` unfiltered with `before=` pagination (16 rows in window; first page oldest `2026-08-20T03:45Z` so window fully covered — no further pages needed), `GET /audit-log?action=agent&since=2026-08-24T06:47:38Z` (8 rows, `next_cursor` null), `GET /engine-health`, `GET /inbox/counts`, `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `GET /admin/ticket-snapshot/ELS-390` + `ELS-391` + `ELS-392` + `ELS-393`, `gh pr list` on that repo (47 open). `GET /local-tracker/dashboard` returned **404** (not used). `action=tracker` returned **422** (use `action=agent` or unfiltered — invented prefixes like `tracker`/`scheduled`/`pr_merge`/`workflow` are rejected).

### Ticket movement (24h)

Movement was light: **ELS-393** (today's daily review, in-flight), **weekly-audit** W35 inbox + leaf finish, and **Daily digest — 2026-08-24**. No daily-review tickets for **2026-08-23** or **2026-08-24** in this window (weekend gap). **ELS-392 / ELS-391 / ELS-390** had **no** new agent events in this window but remain stuck (carryover).

- **ELS-393**: `scheduled_routine.ticket_created` @ 2026-08-25T06:30 (`routine_kind` `daily`, `period_key` `2026-08-25`, `ticket_ref` ELS-393); `tracker.event.received` / `agent_run.dispatch` `planning` @ 2026-08-25T06:40; `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-25T06:43; `agent_run.dispatch` `dev_implementation` @ 2026-08-25T06:43 — **in-flight** (this report), not stuck. Linear state **In Progress**
- **weekly-audit**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` `enumerate` @ 2026-08-25T03:30; `agent_run.inbox_item` "Weekly audit — 2026-W35" @ 2026-08-25T03:37; `agent_run.finish` `workflow_step` `ready_next_step` @ 2026-08-25T03:37 (`workflow_leaf` true; no `ticket_ref`); follow-on steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-25T03:45
- **Daily digest — 2026-08-24**: `agent_run.dispatch` `daily-digest` @ 2026-08-24T09:00; `agent_run.inbox_item` "Daily digest — 2026-08-24" @ 2026-08-24T09:04; `agent_run.finish` `workspace_daily` `ready_next_step` @ 2026-08-24T09:07 (`noop:no_ticket`)
- **0** `pr_merge.tracker_done` rows in window
- **ELS-392 / ELS-391 / ELS-390**: no new `agent_run.*` in window (carryover stuck — see Stuck / attention)

### Stuck / attention

- **ELS-392**: still blocked at `code_review` (`no_approval` on PR **#466**); Linear state **Review**, labels include `blocked`; Ship BLOCKER comment @ 2026-08-22T06:59; no new agent events in this window
- **ELS-391**: still blocked at `code_review` (`no_approval` on PR **#465**); Linear state **Review**, labels include `blocked`; no new agent events in this window
- **ELS-390**: still blocked at `code_review` (`no_approval` on PR **#464**); Linear state **Review**, labels include `blocked`; no new agent events in this window
- **weekly-audit**: inbox report "Weekly audit — 2026-W35" filed @ 2026-08-25T03:37 (`agent_run.inbox_item`); leaf finish `ready_next_step` @ 2026-08-25T03:37
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-25T06:43:52Z — ELS-393 planning→dev handoff)
- Inbox counts: `all_open=208` (`by_status.new=208`); `by_type` report=148, blocker=59, improvement=1, stuck=0, clarification=0; `mine=0`, `unassigned=208`
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship` (no drift)
- Open PRs: **47** (all awaiting review; CI green on rollup; no failed checks). Newest #466 (ELS-392); oldest #417 (ELS-331). No two open PRs share the same ticket id
- **ELS-393** is in-flight daily-review work, not stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (47). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR. Full table (matches 2026-08-19/20/21/22 style).

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#466](https://github.com/ElMundiUA/ship/pull/466) | ELS-392 | ~3d | awaiting review | **green** (7/7 checks) |
| [#465](https://github.com/ElMundiUA/ship/pull/465) | ELS-391 | ~4d | awaiting review | **green** (7/7 checks) |
| [#464](https://github.com/ElMundiUA/ship/pull/464) | ELS-390 | ~5d | awaiting review | **green** (7/7 checks) |
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | ~6d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~7d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~10d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~11d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~12d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~13d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~14d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~17d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~18d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~19d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~20d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~21d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~24d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~25d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~26d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~27d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~28d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~31d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~32d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~33d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~34d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~35d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~38d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~39d | awaiting review | **green** (7/7 checks) |
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~40d | awaiting review | **green** (7/7 checks) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~41d | awaiting review | **green** (7/7 checks) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~42d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~45d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~46d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~48d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~49d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~52d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~53d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~54d | awaiting review | **green** (7/7 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~55d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~56d | awaiting review | **green** (12/12 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~59d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~60d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~61d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~62d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~63d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~66d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~67d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~68d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #466** (ELS-392 daily review for 2026-08-22) — CI is green (7/7); `code_review` stopped on `no_approval`; clear the Linear **blocked** label after approval so validation can advance.
2. Approve **PR #465** / **#464** (ELS-391 / ELS-390) the same way — both CI green, both `blocked` at `code_review` on `no_approval`.
3. Triage inbox report **Weekly audit — 2026-W35** filed @ 2026-08-25T03:37 and decide whether to batch-drain the 47 open daily-review PRs (oldest #417 @ ~68d) or switch future reports to a summary PR table.
