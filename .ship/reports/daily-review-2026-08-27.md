## Daily review — 2026-08-27

_Snapshot generated 2026-08-27 06:50 UTC. Window: last 24h ending at generation time (2026-08-26 06:50:11 UTC → 2026-08-27 06:50:11 UTC)._

Sources: `GET /audit-log` unfiltered with `limit=200` (69 rows in window; first page oldest `2026-08-20T06:45Z` so window fully covered — `next_cursor` present but unused), `GET /audit-log?action=agent&since=2026-08-26T06:48:49Z` (43 rows, `next_cursor` null), `GET /engine-health`, `GET /v1/health`, `GET /inbox/counts` (carryover totals — not `GET /inbox` list defaults), `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `GET /admin/ticket-snapshot/ELS-391` + `ELS-392` + `ELS-393` + `ELS-394` + `ELS-395`, `GET /admin/orphan-tickets`, `gh pr list --state open --json number,title,createdAt,reviewDecision,statusCheckRollup` on that repo (49 open, **full table**). `GET /local-tracker/dashboard` returned **404** (not used). `action=tracker` and `action=scheduled` returned **422** (use `action=agent` or unfiltered — invented prefixes like `tracker`/`scheduled`/`pr_merge`/`workflow` are rejected).

### Ticket movement (24h)

Movement: **ELS-395** (today's daily review, in-flight), **ELS-394** finished a full SDLC pass yesterday ending `code_review` **blocked** on `no_approval` (PR **#468**) with later `overlay_frozen_skipped` at validation, **Weekly audit — 2026-W35** follow-on workflow steps + leaf finish this morning, and **Daily digest — 2026-08-26** yesterday. **ELS-393 / ELS-392 / ELS-391** (and older daily-review PRs) had **no** new agent events in this window but remain stuck carryover. **0** `pr_merge.tracker_done` rows in window.

- **ELS-395**: `scheduled_routine.ticket_created` @ 2026-08-27T06:30 (`routine_kind` `daily`, `period_key` `2026-08-27`, `ticket_ref` ELS-395, `target_fsm_stage` `planning`); `tracker.event.received` / `agent_run.dispatch` `planning` @ 2026-08-27T06:43; `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-27T06:47; `agent_run.dispatch` `dev_implementation` @ 2026-08-27T06:47 — **in-flight** (this report), not stuck. Linear state **In Progress**, labels include `stage:planning`
- **ELS-394**: `agent_run.finish` `dev_implementation` `ready_next_step` → `qa_manual` @ 2026-08-26T06:52; `agent_run.finish` `validation` `ready_next_step` → `code_review` @ 2026-08-26T07:03; `agent_run.finish` `code_review` `blocked` with `phase4:rejected:no_approval` + `tracker:label:blocked` @ 2026-08-26T07:06 (PR **#468**); `agent_run.overlay_frozen_skipped` at `validation` @ 2026-08-26T07:23 (`matched_labels` includes `blocked`). Linear state **Review**, labels include `blocked`
- **Weekly audit — 2026-W35**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` `enumerate` @ 2026-08-27T03:30 (`routine_id` `weekly-audit`); `agent_run.inbox_item` "Weekly audit — 2026-W35" @ 2026-08-27T03:37; `agent_run.finish` workflow leaf `ready_next_step` @ 2026-08-27T03:38; follow-on steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-27T03:45
- **Daily digest — 2026-08-26**: `agent_run.dispatch` `daily-digest` @ 2026-08-26T09:00; inbox items @ 2026-08-26T09:06–09:10; `agent_run.finish` `workspace_daily` `ready_next_step` → `workspace_daily_done` @ 2026-08-26T09:11 (`noop:no_ticket`)
- **ELS-393 / ELS-392 / ELS-391**: no new `agent_run.*` in window (carryover stuck — see Stuck / attention)

### Stuck / attention

- **ELS-394**: blocked at `code_review` (`no_approval` on PR **#468**); Linear **Review**, labels include `blocked`; `overlay_frozen_skipped` at `validation` @ 2026-08-26T07:23 — fresh blocked finish in this window; CI on #468 is **7/7 SUCCESS** (awaiting review/approval)
- **ELS-393**: still blocked at `code_review` (`no_approval` on PR **#467**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-392**: still blocked at `code_review` (`no_approval` on PR **#466**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-391**: still blocked at `code_review` (`no_approval` on PR **#465**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **weekly-audit**: inbox report "Weekly audit — 2026-W35" filed @ 2026-08-27T03:37; leaf finish `ready_next_step` @ 2026-08-27T03:38; follow-on audit steps dispatched @ 2026-08-27T03:45
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover; one fresh `outcome=blocked` finish in window: ELS-394)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-27T06:47:13Z — ELS-395 planning→dev handoff). API `/v1/health`: `status=ok`, `database=ok`
- Inbox counts (`/inbox/counts`): `all_open=221` (`by_status.new=221`); `by_type` report=154, blocker=61, improvement=6, stuck=0, clarification=0; `mine=0`, `unassigned=221`
- Orphans: admin orphan list includes **51** prior daily-review tickets (mostly **Review**); audit shows repeated `agent_run.orphan_skipped` with `reason=no_project_id` for older daily reviews (ELS-345–354, ELS-265) still firing in this window
- Tracker (priorities): Linear **connected** (`last_health_error` null); `autonomy_paused=false`
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship` (no drift)
- Open PRs: **49** (all awaiting review; CI green on rollup; no failed checks). Newest #468 (ELS-394); oldest #417 (ELS-331). No two open PRs share the same ticket id
- **ELS-395** is in-flight daily-review work, not stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (49). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR. Full table (matches 2026-08-19/20/21/22/25/26 style).

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#468](https://github.com/ElMundiUA/ship/pull/468) | ELS-394 | ~23h | awaiting review | **green** (7/7 checks) |
| [#467](https://github.com/ElMundiUA/ship/pull/467) | ELS-393 | ~2d | awaiting review | **green** (7/7 checks) |
| [#466](https://github.com/ElMundiUA/ship/pull/466) | ELS-392 | ~5d | awaiting review | **green** (7/7 checks) |
| [#465](https://github.com/ElMundiUA/ship/pull/465) | ELS-391 | ~5d | awaiting review | **green** (7/7 checks) |
| [#464](https://github.com/ElMundiUA/ship/pull/464) | ELS-390 | ~7d | awaiting review | **green** (7/7 checks) |
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | ~7d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~8d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~12d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~13d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~13d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~15d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~15d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~19d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~19d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~21d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~21d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~22d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~26d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~27d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~27d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~29d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~30d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~33d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~33d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~35d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~35d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~37d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~40d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~41d | awaiting review | **green** (7/7 checks) |
| [#439](https://github.com/ElMundiUA/ship/pull/439) | ELS-357 | ~42d | awaiting review | **green** (7/7 checks) |
| [#438](https://github.com/ElMundiUA/ship/pull/438) | ELS-356 | ~43d | awaiting review | **green** (7/7 checks) |
| [#437](https://github.com/ElMundiUA/ship/pull/437) | ELS-355 | ~44d | awaiting review | **green** (7/7 checks) |
| [#436](https://github.com/ElMundiUA/ship/pull/436) | ELS-354 | ~46d | awaiting review | **green** (7/7 checks) |
| [#435](https://github.com/ElMundiUA/ship/pull/435) | ELS-347 | ~48d | awaiting review | **green** (7/7 checks) |
| [#434](https://github.com/ElMundiUA/ship/pull/434) | ELS-345 | ~49d | awaiting review | **green** (7/7 checks) |
| [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 | ~51d | awaiting review | **green** (7/7 checks) |
| [#431](https://github.com/ElMundiUA/ship/pull/431) | ELS-343 | ~54d | awaiting review | **green** (7/7 checks) |
| [#430](https://github.com/ElMundiUA/ship/pull/430) | ELS-342 | ~55d | awaiting review | **green** (7/7 checks) |
| [#429](https://github.com/ElMundiUA/ship/pull/429) | ELS-341 | ~56d | awaiting review | **green** (7/7 checks) |
| [#426](https://github.com/ElMundiUA/ship/pull/426) | ELS-340 | ~57d | awaiting review | **green** (7/7 checks) |
| [#425](https://github.com/ElMundiUA/ship/pull/425) | ELS-339 | ~58d | awaiting review | **green** (12/12 checks) |
| [#424](https://github.com/ElMundiUA/ship/pull/424) | ELS-338 | ~61d | awaiting review | **green** (7/7 checks) |
| [#423](https://github.com/ElMundiUA/ship/pull/423) | ELS-337 | ~62d | awaiting review | **green** (7/7 checks) |
| [#422](https://github.com/ElMundiUA/ship/pull/422) | ELS-336 | ~63d | awaiting review | **green** (7/7 checks) |
| [#421](https://github.com/ElMundiUA/ship/pull/421) | ELS-335 | ~64d | awaiting review | **green** (7/7 checks) |
| [#420](https://github.com/ElMundiUA/ship/pull/420) | ELS-334 | ~65d | awaiting review | **green** (7/7 checks) |
| [#419](https://github.com/ElMundiUA/ship/pull/419) | ELS-333 | ~68d | awaiting review | **green** (7/7 checks) |
| [#418](https://github.com/ElMundiUA/ship/pull/418) | ELS-332 | ~69d | awaiting review | **green** (7/7 checks) |
| [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 | ~70d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #468** (ELS-394 daily review for 2026-08-26) — CI is green (7/7); `code_review` stopped on `no_approval`; clear the Linear **blocked** label after approval so validation can advance past `overlay_frozen_skipped`.
2. Approve **PR #467** / **#466** / **#465** (ELS-393 / ELS-392 / ELS-391) the same way — all CI green, all `blocked` at `code_review` on `no_approval` (quiet carryover this window).
3. Triage inbox report **Weekly audit — 2026-W35** filed @ 2026-08-27T03:37 and decide whether to batch-drain the 49 open daily-review PRs (oldest #417 @ ~70d) or switch future reports to a summary PR table.

