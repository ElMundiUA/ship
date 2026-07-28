## Daily review — 2026-07-28

_Snapshot generated 2026-07-28T06:39:30Z. Window: last 24h ending at generation time (`since=2026-07-27T06:38:05Z`). Sources: Ship workspace API (audit-log, inbox/counts, engine-health, processes, repos, admin/orphan-tickets) + `gh` PR check rollups on `ElMundiUA/ship`. Linear was not read via MCP._

### Ticket movement (24h)

Quiet ticket-SDLC window aside from today’s daily-review ticket and scheduled digests/audits.

- **ELS-370** (this ticket): created by `scheduled_routine.ticket_created` @ 2026-07-28T06:30:02Z (`period_key=2026-07-28`, routine `daily`); planning finished `ready_next_step` → `dev_implementation` @ 2026-07-28T06:36:14Z (`agent_run.finish` run_f94126505e32f3dd); tracker moved Backlog → In Progress (`tracker.event.received` @ 2026-07-28T06:37:08Z). **In-flight** — this report (not stuck).
- **workspace_daily** / daily-digest: dispatched @ 2026-07-27T09:00:10Z; finished `ready_next_step` (`noop:no_ticket`) @ 2026-07-27T09:06:38Z. Inbox items attested via audit: “Daily digest — 2026-07-27” (@ 09:03 and 09:05) plus “Daily digest — 2026-07-27 (id-probe)” @ 09:04 — first inbox list page may omit these (paging caveat).
- **workspace_weekly** / weekly-audit: `workflow.coding_leaf.dispatched` enumerate @ 2026-07-28T03:30:03Z; leaf finish `ready_next_step` @ 03:39:08Z; parallel audit steps dispatched @ 03:40:00Z (`rank`, `audit.test-gaps`, `audit.coupling`, `audit.complexity`). Inbox: “Weekly audit — 2026-W31” filed twice (@ 03:36:52Z, 03:38:33Z) per `agent_run.inbox_item`.
- **Operator inbox actions** @ 2026-07-27T09:05–09:06: one report `inbox.disposition.dismiss`; one `inbox.decide` resolved with selections `finding-01`…`finding-03` (no tickets created).
- **No PR merges** and **no `pr_merge.tracker_done`** in the window. Unfiltered `audit-log?since=` returned 24 rows (not empty).

### Stuck / attention

- **Frozen daily-review pile (carryover):** 27 tickets in Review with `blocked` + validation-stage labels (ELS-331…ELS-369, excluding weekends/gaps) — pipeline frozen at validation awaiting human merge/approval; no fresh `overlay_frozen_skipped` or `outcome=blocked` finishes in this 24h window.
- **ELS-346** (Daily review — 2026-07-09): still **Backlog** with only `stage:planning` — no open PR in the stack; stalled vs siblings that reached Review.
- **Backlog product orphans (no stage movement):** ELS-322 (Bug — picker frozen-ticket resume), ELS-319 / ELS-318 (Feature — Navigator / connect-agent). Not daily-review; no movement in window.
- **Development process health:** **degraded** (`blocked_count=25` on primary `development` process — matches frozen daily-review projection stack).
- **Engine health:** **healthy** (`expired_unswept_locks=0`, `active_locks=1`, `last_dispatch_at`/`last_finish_at` 2026-07-28T06:36:14Z, `stalled=[]`).
- **Bundle drift:** none (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`).
- **Inbox:** `by_status.new=130` (`reports_new=92`, `blocker=38`); actionable pile is mostly stale carryover — newest digest/weekly titles may not appear on the first inbox page (see audit attestation above).

### PRs

**27 open PRs**, all daily-review report PRs; **CI: 27 green / 0 red / 0 pending** (`gh` statusCheckRollup). All awaiting human review/merge — none instruct agents to merge.

| Anchor | PR | Ticket | Created | CI |
|--------|-----|--------|---------|-----|
| Oldest | [#417](https://github.com/ElMundiUA/ship/pull/417) | ELS-331 (2026-06-18) | 2026-06-18 | **green** |
| Mid | [#432](https://github.com/ElMundiUA/ship/pull/432) | ELS-344 (2026-07-07) | 2026-07-07 | **green** |
| Newest | [#446](https://github.com/ElMundiUA/ship/pull/446) | ELS-369 (2026-07-25) | 2026-07-25 | **green** |

No non–daily-review open PRs. No red CI to call out. ELS-370’s PR is expected from this run (not yet in the open list at snapshot time).

### Next actions

1. Human-decide the **27-PR daily-review stack** (merge oldest-first, bulk-close, or leave) — all CI green; agents must not merge.
2. Triage **Weekly audit — 2026-W31** inbox report (duplicate `agent_run.inbox_item` rows @ 03:36–03:38) and confirm weekly workflow leaf completed cleanly.
3. Unstick or close **ELS-346** (2026-07-09 daily review still Backlog / planning-only) once the Review-stack policy is chosen; let **ELS-370** finish QA after this PR lands.
