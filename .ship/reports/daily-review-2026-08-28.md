## Daily review — 2026-08-28

_Snapshot generated 2026-08-28 06:37 UTC. Window: last 24h ending at generation time (2026-08-27 06:37:06 UTC → 2026-08-28 06:37:13 UTC)._

Sources: `GET /audit-log` unfiltered with `limit=200` (38 rows in window; oldest on first page `2026-08-21T06:53Z` so window fully covered — `next_cursor` present but unused), `GET /audit-log?action=agent&since=2026-08-27T06:37:06Z` (18 rows, `next_cursor` null), `GET /engine-health`, `GET /v1/health`, `GET /inbox/counts` (carryover totals — not `GET /inbox/items` list defaults), `GET /processes/development`, `GET /repos` (`ElMundiUA/ship`), `GET /priorities`, `GET /admin/ticket-snapshot/ELS-391` + `ELS-392` + `ELS-393` + `ELS-394` + `ELS-395` + `ELS-396`, `GET /admin/orphan-tickets`, `gh pr list --state open --json number,title,createdAt,reviewDecision,statusCheckRollup` on that repo (30 open, **full table**). `GET /local-tracker/dashboard` returned **404** (not used). `action=tracker` and `action=scheduled` returned **422** (use `action=agent` or unfiltered — invented prefixes like `tracker`/`scheduled`/`pr_merge`/`workflow` are rejected).

### Ticket movement (24h)

Movement: **ELS-396** (today's daily review, in-flight), **ELS-395** finished a full SDLC pass yesterday ending `code_review` **blocked** on `no_approval` (PR **#469**) with later `overlay_frozen_skipped` at validation, **Weekly audit — 2026-W35** leaf finish + follow-on workflow steps this morning, and **Daily digest — 2026-08-27** yesterday. **ELS-394 / ELS-393 / ELS-392 / ELS-391** (and older daily-review PRs) had **no** new agent events in this window but remain stuck carryover. **0** `pr_merge.tracker_done` rows in window.

- **ELS-396**: `scheduled_routine.ticket_created` @ 2026-08-28T06:30 (`routine_kind` `daily`, `period_key` `2026-08-28`, `ticket_ref` ELS-396, `target_fsm_stage` `planning`); `tracker.event.received` / `agent_run.dispatch` `planning` @ 2026-08-28T06:32; `agent_run.finish` `planning` `ready_next_step` (`stage_next` `dev_implementation`) @ 2026-08-28T06:34; `agent_run.dispatch` `dev_implementation` @ 2026-08-28T06:34 — **in-flight** (this report), not stuck. Linear state **In Progress**, labels include `stage:planning`
- **ELS-395**: `agent_run.finish` `dev_implementation` `ready_next_step` → `qa_manual` @ 2026-08-27T06:51; `agent_run.finish` `validation` `ready_next_step` → `code_review` @ 2026-08-27T07:01; `agent_run.finish` `code_review` `blocked` with `phase4:rejected:no_approval` + `tracker:label:blocked` @ 2026-08-27T07:05 (PR **#469**); `agent_run.overlay_frozen_skipped` at `validation` @ 2026-08-27T07:13 (`matched_labels` includes `blocked`). Linear state **Review**, labels include `blocked`
- **Weekly audit — 2026-W35**: `workflow.coding_leaf.dispatched` / `workflow.step_dispatched` `enumerate` @ 2026-08-28T03:30 (`routine_id` `weekly-audit`); `agent_run.inbox_item` "Weekly audit — 2026-W35" @ 2026-08-28T03:32; `agent_run.finish` workflow leaf `ready_next_step` @ 2026-08-28T03:32; follow-on steps (`audit.complexity`, `audit.coupling`, `audit.test-gaps`, `rank`) dispatched @ 2026-08-28T03:45. _(Prior W35 run @ 2026-08-27T03:30–03:38 predates this window's start by ~3h — see Stuck / attention.)_
- **Daily digest — 2026-08-27**: `agent_run.dispatch` `daily-digest` @ 2026-08-27T09:00; inbox item @ 2026-08-27T09:04; `agent_run.finish` `workspace_daily` `ready_next_step` → `workspace_daily_done` @ 2026-08-27T09:05
- **ELS-394 / ELS-392 / ELS-391**: no new `agent_run.*` in window (carryover stuck — see Stuck / attention)

### Stuck / attention

- **ELS-395**: blocked at `code_review` (`no_approval` on PR **#469**); Linear **Review**, labels include `blocked`; `overlay_frozen_skipped` at `validation` @ 2026-08-27T07:13 — fresh blocked finish in this window; CI on #469 is **7/7 SUCCESS** (awaiting review/approval)
- **ELS-394**: still blocked at `code_review` (`no_approval` on PR **#468**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-393**: still blocked at `code_review` (`no_approval` on PR **#467**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-392**: still blocked at `code_review` (`no_approval` on PR **#466**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **ELS-391**: still blocked at `code_review` (`no_approval` on PR **#465**); Linear **Review**, labels include `blocked`; no new agent events in this window
- **weekly-audit W35**: inbox report "Weekly audit — 2026-W35" filed @ 2026-08-28T03:32 (in-window); prior run @ 2026-08-27T03:37 also filed W35 inbox item (predates window start by ~3h, same ISO week)
- Development process health: **degraded** (`blocked_count=25`, `task_count=25` — mostly historical `code_review` / `no_approval` carryover; one fresh `outcome=blocked` finish in window: ELS-395)
- Engine health: **healthy** (`stalled=[]`, `expired_unswept_locks=0`, `active_locks=1`; `last_dispatch_at` / `last_finish_at` 2026-08-28T06:34:47Z — ELS-396 planning→dev handoff). API `/v1/health`: `status=ok`, `database=ok`
- Inbox counts (`/inbox/counts`): `all_open=224` (`by_status.new=224`); `by_type` report=156, blocker=62, improvement=6, stuck=0, clarification=0; `mine=0`, `unassigned=224`
- Orphans: admin orphan list includes **55** tickets (**52** daily-review titles, `project_id` null); **ELS-396** (In Progress/planning) and **ELS-395** (Review/blocked) among them
- Tracker (priorities): Linear **connected** (`last_health_error` null); `autonomy_paused=false`
- Bundle: `installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship` (no drift)
- Open PRs: **30** (all awaiting review; CI green on rollup; no failed checks). Newest #469 (ELS-395); oldest #440 (ELS-358). No two open PRs share the same ticket id
- **ELS-396** is in-flight daily-review work, not stuck

### PRs

Open PRs on activated repo `ElMundiUA/ship` (30). Review decision empty on all → **awaiting review**. CI from GitHub check rollup (not guessed). No failed checks on any open PR. Full table (matches prior August daily-review style).

| PR | Ticket | Age | Review | CI |
|----|--------|-----|--------|-----|
| [#469](https://github.com/ElMundiUA/ship/pull/469) | ELS-395 | ~23h | awaiting review | **green** (7/7 checks) |
| [#468](https://github.com/ElMundiUA/ship/pull/468) | ELS-394 | ~47h | awaiting review | **green** (7/7 checks) |
| [#467](https://github.com/ElMundiUA/ship/pull/467) | ELS-393 | ~2d | awaiting review | **green** (7/7 checks) |
| [#466](https://github.com/ElMundiUA/ship/pull/466) | ELS-392 | ~5d | awaiting review | **green** (7/7 checks) |
| [#465](https://github.com/ElMundiUA/ship/pull/465) | ELS-391 | ~6d | awaiting review | **green** (7/7 checks) |
| [#464](https://github.com/ElMundiUA/ship/pull/464) | ELS-390 | ~7d | awaiting review | **green** (7/7 checks) |
| [#463](https://github.com/ElMundiUA/ship/pull/463) | ELS-389 | ~8d | awaiting review | **green** (7/7 checks) |
| [#462](https://github.com/ElMundiUA/ship/pull/462) | ELS-388 | ~9d | awaiting review | **green** (7/7 checks) |
| [#461](https://github.com/ElMundiUA/ship/pull/461) | ELS-387 | ~12d | awaiting review | **green** (7/7 checks) |
| [#460](https://github.com/ElMundiUA/ship/pull/460) | ELS-386 | ~13d | awaiting review | **green** (7/7 checks) |
| [#459](https://github.com/ElMundiUA/ship/pull/459) | ELS-385 | ~14d | awaiting review | **green** (7/7 checks) |
| [#458](https://github.com/ElMundiUA/ship/pull/458) | ELS-384 | ~15d | awaiting review | **green** (7/7 checks) |
| [#457](https://github.com/ElMundiUA/ship/pull/457) | ELS-383 | ~16d | awaiting review | **green** (7/7 checks) |
| [#456](https://github.com/ElMundiUA/ship/pull/456) | ELS-382 | ~19d | awaiting review | **green** (7/7 checks) |
| [#455](https://github.com/ElMundiUA/ship/pull/455) | ELS-381 | ~20d | awaiting review | **green** (7/7 checks) |
| [#454](https://github.com/ElMundiUA/ship/pull/454) | ELS-380 | ~21d | awaiting review | **green** (7/7 checks) |
| [#453](https://github.com/ElMundiUA/ship/pull/453) | ELS-379 | ~22d | awaiting review | **green** (7/7 checks) |
| [#452](https://github.com/ElMundiUA/ship/pull/452) | ELS-378 | ~23d | awaiting review | **green** (7/7 checks) |
| [#451](https://github.com/ElMundiUA/ship/pull/451) | ELS-376 | ~26d | awaiting review | **green** (7/7 checks) |
| [#450](https://github.com/ElMundiUA/ship/pull/450) | ELS-375 | ~27d | awaiting review | **green** (7/7 checks) |
| [#449](https://github.com/ElMundiUA/ship/pull/449) | ELS-374 | ~28d | awaiting review | **green** (7/7 checks) |
| [#448](https://github.com/ElMundiUA/ship/pull/448) | ELS-371 | ~29d | awaiting review | **green** (7/7 checks) |
| [#447](https://github.com/ElMundiUA/ship/pull/447) | ELS-370 | ~30d | awaiting review | **green** (7/7 checks) |
| [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 | ~33d | awaiting review | **green** (7/7 checks) |
| [#445](https://github.com/ElMundiUA/ship/pull/445) | ELS-368 | ~34d | awaiting review | **green** (7/7 checks) |
| [#444](https://github.com/ElMundiUA/ship/pull/444) | ELS-365 | ~35d | awaiting review | **green** (7/7 checks) |
| [#443](https://github.com/ElMundiUA/ship/pull/443) | ELS-363 | ~36d | awaiting review | **green** (7/7 checks) |
| [#442](https://github.com/ElMundiUA/ship/pull/442) | ELS-362 | ~37d | awaiting review | **green** (7/7 checks) |
| [#441](https://github.com/ElMundiUA/ship/pull/441) | ELS-361 | ~40d | awaiting review | **green** (7/7 checks) |
| [#440](https://github.com/ElMundiUA/ship/pull/440) | ELS-358 | ~41d | awaiting review | **green** (7/7 checks) |

### Next actions

1. Approve and merge **PR #469** (ELS-395 daily review for 2026-08-27) — CI is green (7/7); `code_review` stopped on `no_approval`; clear the Linear **blocked** label after approval so validation can advance past `overlay_frozen_skipped`.
2. Approve **PR #468** / **#467** / **#466** / **#465** (ELS-394 / ELS-393 / ELS-392 / ELS-391) the same way — all CI green, all `blocked` at `code_review` on `no_approval` (quiet carryover this window).
3. Triage inbox report **Weekly audit — 2026-W35** filed @ 2026-08-28T03:32 and decide whether to batch-drain the 30 open daily-review PRs (oldest #440 @ ~41d) or switch future reports to a summary PR table.
