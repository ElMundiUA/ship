## Daily review — 2026-08-29

_Snapshot generated 2026-08-29 06:46 UTC. Window: last 24h ending at generation time (2026-08-28 06:46:23 UTC → 2026-08-29 06:46:23 UTC)._

Sources: `GET /audit-log` unfiltered with `limit=200` (29 rows in window; oldest on first page `2026-08-22T06:57Z` so window fully covered — `next_cursor` present but unused), `GET /audit-log?action=agent&since=2026-08-28T06:46:23Z` (11 rows, `next_cursor` null), `GET /engine-health`, `GET /v1/health`, `GET /inbox/counts` (carryover totals — not inbox item list defaults), `GET /processes` + `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `GET /admin/ticket-snapshot/ELS-392` … `ELS-397`, `GET /admin/orphan-tickets`, `gh pr list --state open --json number,title,createdAt,reviewDecision,statusCheckRollup` on that repo (51 open, **full table**). `GET /local-tracker/dashboard` returned **404** (not used). Workspace-scoped `GET .../health` returned **404** (used `GET /v1/health` instead). `action=tracker`, `action=scheduled`, `action=pr_merge`, and `action=workflow` returned **422** (use `action=agent` or unfiltered — invented prefixes are rejected).

### Ticket movement (24h)

Movement: **ELS-397** (today's daily review, in-flight), **ELS-396** finished validation → `code_review` then `code_review` **blocked** on `no_approval` (PR **#470**) yesterday morning, **Weekly audit — 2026-W35** leaf finish + follow-on workflow steps this morning, and **Daily digest — 2026-08-28** yesterday. **ELS-395 / ELS-394 / ELS-393 / ELS-392** (and older daily-review PRs) had **no** new agent events in this window but remain stuck carryover. **0** `pr_merge.tracker_done` rows in window.

- **ELS-397**: `scheduled_routine.ticket_created` @ 2026-08-29T06:30 (`routine_kind` `daily`, `period_key` `2026-08-29`, `ticket_ref` ELS-397, `target_fsm_stage` `planning`); `tracker.event.received` / `agent_run.dispatch` `planning` @ 2026-08-29T06:40; `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-29T06:44; `agent_run.dispatch` `dev_implementation` @ 2026-08-29T06:44; Linear Backlog→**In Progress** @ 2026-08-29T06:45 — **in-flight** (this report), not stuck. Labels include `stage:planning`; `project_id` null
- **ELS-396**: `agent_run.finish` `validation` `ready_next_step` → `code_review` @ 2026-08-28T06:47; `agent_run.finish` `code_review` `blocked` with `phase4:rejected:no_approval` + `tracker:label:blocked` @ 2026-08-28T06:49 (PR **#470**); `transition.validation_failed` reason `no_approval` (expected `auto_merge`). Linear state **Review**, labels include `blocked`
- **Weekly audit — 2026-W35**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` `enumerate` @ 2026-08-29T03:30 (`routine_id` `weekly-audit`); `agent_run.inbox_item` "Weekly audit — 2026-W35" @ 2026-08-29T03:41; `agent_run.finish` workflow leaf `ready_next_step` @ 2026-08-29T03:41; follow-on steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-29T03:50
- **Daily digest — 2026-08-28**: `agent_run.dispatch` `daily-digest` @ 2026-08-28T09:00; inbox item @ 2026-08-28T09:04; `agent_run.finish` `workspace_daily` `ready_next_step` → `workspace_daily_done` @ 2026-08-28T09:05
- **ELS-395 / ELS-394 / ELS-393 / ELS-392**: no new `agent_run.*` in window (carryover stuck — see Stuck / attention)

### Stuck / attention

- **ELS-396**: blocked at `code_review` (`no_approval` on PR **#470**); Linear **Review**, labels include `blocked` — freshest blocked finish in this window; CI on #470 is **7/7 SUCCESS** (awaiting review/approval)
- **ELS-395**: still blocked at `code_review` (`no_approval` on PR **#469**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-394**: still blocked at `code_review` (`no_approval` on PR **#468**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-393**: still blocked at `code_review` (`no_approval` on PR **#467**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-392**: still blocked at `code_review` (`no_approval` on PR **#466**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **weekly-audit W35**: inbox report "Weekly audit — 2026-W35" filed @ 2026-08-29T03:41 (in-window run)
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover; one fresh `outcome=blocked` finish in window: ELS-396)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-29T06:44:39Z — ELS-397 planning→dev handoff). API `/v1/health`: `status=ok`, `database=ok`
- Inbox counts (`/inbox/counts`): `all_open=227` (`by_status.new=227`); `by_type` report=158, blocker=63, improvement=6, stuck=0, clarification=0; `mine=0`, `unassigned=227`
- Orphans: admin orphan list includes **56** tickets (**53** daily-review titles, `project_id` null); **ELS-397** (In Progress/planning) and **ELS-396** (Review/blocked) among them
- Tracker (priorities): Linear **connected** (`last_health_error` null); `autonomy_paused=false`
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship` (no drift)
- Open PRs: **51** (up from 30 in the 2026-08-28 report; all awaiting review; CI green on rollup — #425 has 10 SUCCESS + 2 SKIPPED). Newest #470 (ELS-396); oldest #417 (ELS-331 / ~72d). No two open PRs share the same ticket id
- **ELS-397** is in-flight daily-review work, not stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (51). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR. Full table (matches prior August daily-review style).

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#470](https://github.com/ElMundiUA/ship/pull/470) | ELS-396 | ~24h | awaiting review | **green** (7/7 checks) |
| [#469](https://github.com/ElMundiUA/ship/pull/469) | ELS-395 | ~48h | awaiting review | **green** (7/7 checks) |
| [#468](https://github.com/ElMundiUA/ship/pull/468) | ELS-394 | ~3d | awaiting review | **green** (7/7 checks) |
| [#467](https://github.com/ElMundiUA/ship/pull/467) | ELS-393 | ~4d | awaiting review | **green** (7/7 checks) |
| [#466](https://github.com/ElMundiUA/ship/pull/466) | ELS-392 | ~7d | awaiting review | **green** (7/7 checks) |
| [#465](https://github.com/ElMundiUA/ship/pull/465) | ELS-391 | ~8d | awaiting review | **green** (7/7 checks) |
| [#464](https://github.com/ElMundiUA/ship/pull/464) | ELS-390 | ~9d | awaiting review | **green** (7/7 checks) |
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | ~10d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~11d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~14d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~15d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~16d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~17d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~18d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~21d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~22d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~23d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~24d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~25d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~28d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~29d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~30d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~31d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~32d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~35d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~36d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~37d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~38d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~39d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~42d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~43d | awaiting review | **green** (7/7 checks) |
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~44d | awaiting review | **green** (7/7 checks) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~45d | awaiting review | **green** (7/7 checks) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~46d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~49d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~50d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~52d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~53d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~56d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~57d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~58d | awaiting review | **green** (7/7 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~59d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~60d | awaiting review | **green** (10/12 checks; 2 skipped) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~63d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~64d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~65d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~66d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~67d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~70d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~71d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~72d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #470** (ELS-396 daily review for 2026-08-28) — CI is green (7/7); `code_review` stopped on `no_approval`; clear the Linear **blocked** label after approval.
2. Approve **PR #469** / **#468** / **#467** / **#466** (ELS-395 / ELS-394 / ELS-393 / ELS-392) the same way — all CI green, all `blocked` at `code_review` on `no_approval` (quiet carryover this window).
3. Triage inbox report **Weekly audit — 2026-W35** filed @ 2026-08-29T03:41 and decide whether to batch-drain the **51** open daily-review PRs (oldest #417 @ ~72d) or keep accumulating one PR per day.
