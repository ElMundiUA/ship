## Daily review — 2026-06-16

_Snapshot generated 2026-06-16 06:51 UTC. Window: last 24h ending at generation time._

### Ticket movement (24h)

- **ELS-309**: `validation` → `code_review` (`ready_next_step`); then **3×** `code_review` **blocked** @ 2026-06-15T06:48–07:16; PR #402 merged → Done @ 2026-06-15T09:08
- **ELS-326**: created by `scheduled_routine.ticket_created` @ 2026-06-16T06:30 (routine `daily`); `planning` → `dev_implementation` (`ready_next_step` @ 2026-06-16T06:49)
- **workspace_daily**: daily digest completed (`ready_next_step` @ 2026-06-15T09:05); inbox item "Daily digest — 2026-06-15" filed
- **weekly-audit**: workflow leaf dispatched @ 2026-06-16T03:30 (`workspace_weekly` routine); inbox item "Weekly audit — 2026-W25" filed @ 2026-06-16T03:36
- **11 PR merges** (`pr_merge.tracker_done`): #402 (ELS-309), #403/#404 (ELS-316), #405 (ELS-317), #406 (ELS-320), #407 (ELS-321/ELS-323), #408 (ELS-323), #409 (ELS-324), #410 (ELS-325), #411 (ELS-314)
- **26 tickets** moved via `dispatch.no_routine` (tracker poll, no agent finish); **9** confirmed Done via `tracker.event.received`

### Stuck / attention

- **ELS-309**: 3× `code_review` blocked yesterday — **resolved** (PR #402 merged; stale projection entries remain)
- **ELS-265, ELS-194, ELS-295**: legacy `code_review` blocked entries in process projection (no new blocked finishes in window; may be stale)
- Engine stall notified @ 2026-06-15T09:20 (`daily-digest:scheduled`, `expired_not_swept`, 20 min) — engine now **healthy** (`expired_unswept_locks=0`, `active_locks=1`)
- Development process health: **degraded** (16 blocked projection items, mostly resolved-ticket noise + inbox carryover)
- Weekly audit inbox item created @ 2026-06-16T03:36 ("Weekly audit — 2026-W25" — 10 debt tickets filed)
- Bundle drift: **resolved** (`installed_bundle_version` 0.42 = `current_bundle_version` 0.42 on `ElMundiUA/ship`)
- Inbox: 0 new items (`counts_by_status.new=0`; 5 resolved, 11 dismissed carryover)

### PRs

PR queue clear — no open PRs on `ElMundiUA/ship`.

### Next actions

1. Review **Weekly audit — 2026-W25** inbox report and triage the 10 filed debt tickets (FSM bottlenecks, MCP security review, bundle/knowledge refresh).
2. Dismiss or resolve stale `code_review` blocked projections for **ELS-265**, **ELS-194**, and **ELS-295** — no active PRs; projection residue from prior loops.
3. Let **ELS-326** (this daily review) complete dev → QA so today's report lands on `main`.
