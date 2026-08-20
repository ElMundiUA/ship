## Daily review — 2026-08-20

_Snapshot generated 2026-08-20 06:45 UTC. Window: last 24h ending at generation time (2026-08-19 06:45 UTC → 2026-08-20 06:45 UTC)._

Sources: `GET /audit-log?since=` (31 rows unfiltered, `next_cursor` null; 12 rows with `action=agent`), `GET /audit-log?action=agent`, `GET /engine-health`, `GET /inbox/counts?ownership=all`, `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `gh pr list` on that repo. `GET /dashboard` returned 404 (not used). `action=agent_run.finish` returned 422 (use `action=agent` or unfiltered `since=`).

### Ticket movement (24h)

- **ELS-390**: created by `scheduled_routine.ticket_created` @ 2026-08-20T06:30 (`target_id` `daily:2026-08-20`, `routine_kind` `daily`); `tracker.event.received` at `planning` @ 2026-08-20T06:40; `agent_run.dispatch` `planning` (`run_b066c74a70834fb5`) → `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-20T06:42; `agent_run.dispatch` `dev_implementation` (`run_95fbe9cd4354ca9c`) @ 2026-08-20T06:42 — **in-flight** (this report), not stuck
- **ELS-389**: `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-19T06:53; `agent_run.dispatch` / `agent_run.finish` `dev_implementation` `ready_next_step` (`stage_next` `qa_manual`) @ 2026-08-19T07:00; `agent_run.dispatch` `qa_manual` (`run_424f4994a48e13ed`); `tracker.event.received` at `validation` @ 2026-08-19T07:26; `agent_run.finish` `validation` `ready_next_step` (`stage_next` `code_review`) @ 2026-08-19T07:18; `agent_run.dispatch` `code_review` (`run_db92fc84b5b03104`); `agent_run.finish` `code_review` **blocked** @ 2026-08-19T07:31 (`phase4:rejected:no_approval`; PR https://github.com/ElMundiUA/ship/pull/463); `transition.validation_failed` reason `no_approval`; `notify.emit` inbox blocker `ELS-389 blocked at code_review`; `dispatch.no_routine`
- **weekly-audit**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` (`enumerate`, `run_88ec2ec33b07089d`) @ 2026-08-20T03:30; `agent_run.inbox_item` "Weekly audit — 2026-W34" @ 2026-08-20T03:32; `agent_run.finish` `workflow_step` `ready_next_step` @ 2026-08-20T03:32 (`workflow_leaf` true; no `ticket_ref`); follow-on audit steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-20T03:45
- **0** `pr_merge.tracker_done` rows in window
- Audit page did not hit the 200 limit (`next_cursor` was null)

### Stuck / attention

- **ELS-389**: `code_review` blocked @ 2026-08-19T07:31 (`no_approval` on PR #463); inbox blocker `ELS-389 blocked at code_review` created in-window
- **weekly-audit**: inbox report "Weekly audit — 2026-W34" filed @ 2026-08-20T03:32 (`agent_run.inbox_item`); leaf `run_88ec2ec33b07089d` finished `ready_next_step` @ 2026-08-20T03:32
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-20T06:42:53Z)
- Inbox counts (`ownership` workspace-wide): `all_open=201` (`status=new`); `by_type` blocker=56, report=144, stuck=0, improvement=1, clarification=0; `mine=0`. Blockers are largely historical daily-review `no_approval` letters; newest in-window: ELS-389 @ 2026-08-19T07:31
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`
- No two open PRs share the same ticket id (44 open PRs → 44 tickets); all awaiting review, CI green on rollup
- **ELS-390** appears in dispatch/finish rows above as in-flight daily-review work, not as stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (44). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR.

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | <1d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~1d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~5d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~6d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~6d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~7d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~8d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~11d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~12d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~14d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~14d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~15d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~18d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~20d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~20d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~22d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~23d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~26d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~26d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~28d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~28d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~30d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~33d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~33d | awaiting review | **green** (7/7 checks) |
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~34d | awaiting review | **green** (7/7 checks) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~35d | awaiting review | **green** (7/7 checks) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~36d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~39d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~41d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~42d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~44d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~47d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~47d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~49d | awaiting review | **green** (7/7 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~50d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~50d | awaiting review | **green** (10 success + 2 skipped) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~54d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~54d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~55d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~57d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~58d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~60d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~62d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~62d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #463** (ELS-389 daily review for 2026-08-19) — CI is green (7/7); `code_review` stopped on `no_approval`.
2. Clear the Linear **blocked** label on **ELS-389** (inbox `blocked:ELS-389:code_review` @ 2026-08-19T07:31) after that approval so the gate can retry.
3. Triage inbox report **Weekly audit — 2026-W34** filed @ 2026-08-20T03:32 and decide whether to batch-approve the 44 open daily-review PRs (oldest #417 @ ~62d) or adopt a summary-table format for future reports.
