## Daily review — 2026-08-22

_Snapshot generated 2026-08-22 06:47 UTC. Window: last 24h ending at generation time (2026-08-21 06:47:09 UTC → 2026-08-22 06:47:09 UTC)._

Sources: `GET /audit-log?since=2026-08-21T06:47:09Z` (33 rows unfiltered, `next_cursor` null — full window coverage), `GET /audit-log?action=agent&since=…` (14 rows), `GET /engine-health`, `GET /inbox/counts`, `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `GET /admin/ticket-snapshot/ELS-390` + `ELS-391` + `ELS-392`, `gh pr list` on that repo (46 open). `GET /local-tracker/dashboard` returned **404** (not used). `action=tracker` and `action=scheduled_routine.ticket_created` returned **422** (use `action=agent` or unfiltered `since=` — invented prefixes like `tracker`/`scheduled`/`pr_merge`/`workflow` are rejected).

### Ticket movement (24h)

Movement was light: **ELS-392** (today's daily review, in-flight), **ELS-391** carryover progression through validation/code_review (blocked), and **weekly-audit** W34 re-run.

- **ELS-392**: created by `scheduled_routine.ticket_created` @ 2026-08-22T06:30 (`routine_kind` `daily`); `tracker.event.received` / `agent_run.dispatch` `planning` @ 2026-08-22T06:43; `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-22T06:45; `agent_run.dispatch` `dev_implementation` @ 2026-08-22T06:45 — **in-flight** (this report), not stuck. Linear state **In Progress**
- **ELS-391**: `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) + `agent_run.dispatch` `dev_implementation` @ 2026-08-21T06:48; `agent_run.finish` `dev_implementation` `ready_next_step` (`stage_next` `qa_manual`) + `agent_run.dispatch` `qa_manual` @ 2026-08-21T06:53; `agent_run.finish` `validation` `ready_next_step` (`stage_next` `code_review`) + `agent_run.dispatch` `code_review` @ 2026-08-21T07:04; `agent_run.finish` `code_review` **blocked** @ 2026-08-21T07:07; `transition.validation_failed` reason `no_approval`; `notify.emit` inbox blocker `ELS-391 blocked at code_review`; `tracker.event.received` / `agent_run.dispatch` `validation` @ 2026-08-21T07:18; `agent_run.overlay_frozen_skipped` at `validation` (`matched_labels` `blocked`) @ 2026-08-21T07:18; Linear state **Review**, labels include `blocked`; PR **#465** open
- **weekly-audit**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` @ 2026-08-22T03:30; `agent_run.inbox_item` "Weekly audit — 2026-W34" @ 2026-08-22T03:39; `agent_run.finish` `workflow_step` `ready_next_step` @ 2026-08-22T03:40 (`workflow_leaf` true; no `ticket_ref`); follow-on steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-22T03:45
- **0** `pr_merge.tracker_done` rows in window
- Audit page did not hit the limit (`next_cursor` was null)

### Stuck / attention

- **ELS-391**: `code_review` blocked @ 2026-08-21T07:07 (`no_approval` on PR #465); inbox blocker `ELS-391 blocked at code_review`; `overlay_frozen_skipped` at `validation` @ 2026-08-21T07:18 (`blocked` label froze pipeline)
- **ELS-390**: still blocked at `code_review`/`validation` from 2026-08-20 (`no_approval` on PR #464); Linear state **Review**, labels include `blocked`; no new agent events in this window
- **weekly-audit**: inbox report "Weekly audit — 2026-W34" filed @ 2026-08-22T03:39 (`agent_run.inbox_item`); leaf finish `ready_next_step` @ 2026-08-22T03:40
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-22T06:45:49Z — ELS-392 planning→dev handoff)
- Inbox counts: `all_open=205` (`by_status.new=205`); `by_type` report=146, blocker=58, improvement=1, stuck=0, clarification=0; `mine=0`, `unassigned=205`
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship` (no drift)
- Open PRs: **46** (all awaiting review; CI green on rollup; no failed checks). No two open PRs share the same ticket id
- **ELS-392** is in-flight daily-review work, not stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (46). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR. Full table (matches 2026-08-19/20/21 style).

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#465](https://github.com/ElMundiUA/ship/pull/465) | ELS-391 | <1d | awaiting review | **green** (7/7 checks) |
| [#464](https://github.com/ElMundiUA/ship/pull/464) | ELS-390 | ~2d | awaiting review | **green** (7/7 checks) |
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | ~2d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~3d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~7d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~8d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~8d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~10d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~10d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~13d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~14d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~16d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~16d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~17d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~21d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~22d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~22d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~24d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~25d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~28d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~28d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~30d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~30d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~32d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~35d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~36d | awaiting review | **green** (7/7 checks) |
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~36d | awaiting review | **green** (7/7 checks) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~38d | awaiting review | **green** (7/7 checks) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~39d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~41d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~43d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~44d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~46d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~49d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~49d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~51d | awaiting review | **green** (7/7 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~52d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~52d | awaiting review | **green** (12/12 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~56d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~56d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~57d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~59d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~60d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~63d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~64d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~65d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #465** (ELS-391 daily review for 2026-08-21) — CI is green (7/7); `code_review` stopped on `no_approval`.
2. Approve and merge **PR #464** (ELS-390 daily review for 2026-08-20) — CI is green (7/7); clear the Linear **blocked** label after approval so validation can advance past `overlay_frozen_skipped`.
3. Triage inbox report **Weekly audit — 2026-W34** filed @ 2026-08-22T03:39 and decide whether to batch-approve the 46 open daily-review PRs (oldest #417 @ ~65d) or adopt a summary-table format for future reports.
