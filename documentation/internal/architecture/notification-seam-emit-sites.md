# Notification-seam emit-site ledger (ELS-217)

> Authoritative inventory of every place the engine "tells a human something" by
> constructing an `InboxItem` directly, plus the existing engine Linear-comment
> egress sites that serve as reference implementations for the future
> `LinearCommentChannel`. This ledger drives the Phase-1 notification seam
> (ELS-222..226): every **bucket-A** row gets flipped onto `notify()`; **bucket-B**
> rows become the Inbox *channel implementation*; **bucket-C** rows are console
> render-path side-effects that are out of scope for the seam.
>
> Verification: `rg -n 'InboxItem\(' apps/backend/app -t py | grep -v tests`
> returns 15 hits — 14 construction sites below + the class definition at
> `apps/backend/app/db/models/inbox.py:248`.
>
> FOUNDER DEFAULT: launch egress is **inbox-only everywhere**. The target
> `notify()` level below documents intended routing, but actual channel routing
> stays `[inbox]` for every level until a workspace opts in (see ELS-219).

## Bucket A — engine headless egress (flip to `notify()` in Phase 1)

| # | Site | Caller (path type) | Current channel | Level → notify() | Dedup key today | Justification |
|---|------|--------------------|-----------------|------------------|------------------|---------------|
| A1 | `services/dispatcher.py:741` — `_file_no_target_repo_letter` (def :712, invoked from `maybe_dispatch` :1129) | Engine dispatch path (tracker poller / webhook → `maybe_dispatch`) | InboxItem `clarification` / `decision_needed` | **ACTION** | `intake_handle='no-target-repo:<ticket>'` + open-status pre-check at site | Pure engine emission: dispatch routing failed, operator must bind a repo. No HTTP request in the loop. |
| A2 | `api/v1/routes/agent_runs.py:772` — tracker-outage blocker | Pick-tick (`GET …/tracker/next` called by `shipctl run` on a CI runner) | InboxItem `blocker` | **BLOCKER** | Audit-row gate: `agent_run.tracker_next_failed` within `_TRACKER_FAILURE_DEDUP_WINDOW` (1 h) — the InboxItem rides the same window | Engine-critical: the tracker adapter is rejecting calls; agents are stalled. The CLI is the caller but the emission is engine state, not a console action. **Seam note (review risk #1): keep the audit-row dedup in the caller; route only the emit through `notify()`.** |
| A3 | `api/v1/routes/agent_runs.py:1689` — orphan-tickets-skipped improvement | Pick-tick (same `tracker/next` path) | InboxItem `improvement` | **INFO** | None at site (audit rows per orphan exist, but the inbox row is re-emitted per pick that sees orphans) | Engine picker telemetry: tickets without a project were skipped. Candidate for a `notify()`-level dedup key (`orphans:<stage>`), to be added when flipped. |
| A4 | `api/v1/routes/agent_runs.py:4023` — finished-but-no-tracker blocker | Finish handler (`POST …/agent-runs/finish` from `shipctl run`) | InboxItem `blocker` | **BLOCKER** | None (one per finish in this state) | Agent completed work but the workspace has no tracker binding — outcome would otherwise be silently lost. |
| A5 | `api/v1/routes/agent_runs.py:4433` — needs_clarification mirror | Finish handler (same) | InboxItem `clarification` / `decision_needed` | **ACTION** | None (one per `needs_clarification` finish; the paired tracker label `needs_clarification` is the de-facto state) | The agent's question must reach the operator; today it is mirrored to Inbox alongside a Linear comment + signal label — already a two-channel emission, making it the clearest `notify()` candidate. |
| A6 | `api/v1/routes/agent_runs.py:4523` — blocked mirror | Finish handler (same) | InboxItem `blocker` | **BLOCKER** | `intake_handle='blocked:<ticket>:<stage>'` set on the row (no pre-check at site) | Ticket frozen via Linear `blocked` label; operator must unblock. Pairs with the freeze-label egress. |
| A7 | `api/v1/routes/runs.py:572` — `_emit_self_heal_blocker_inbox` (invoked :999) | Routine-run callback (`routine.run.callback`, trusted CI callback path) | InboxItem `blocker` | **BLOCKER** | `source_table='self_heal_run'` + `source_id=<run>` + open-status pre-check | Self-heal failed; engine cannot recover without a human. |
| A8 | `services/sdlc_readiness.py:354` — `file_bootstrap_recommendation` (def :298) | Worker loop (`workers/main.py:78`) | InboxItem `improvement` | **INFO** | `intake_handle='bootstrap_readiness:<repo_id>'` + open-status pre-check | Background worker recommends SDLC setup; informational with an action CTA (`start-bootstrap` action_item). |
| A9 | `services/knowledge_synth.py:384` — auto-routed draft review | Cron `knowledge_synth` (`cron_jobs.py:153`) | InboxItem `improvement` | **ACTION** | None (one per draft created; `source_table='bucket_articles'` + `source_id` identifies the draft) | Knowledge pipeline produced a draft that needs operator review (accept → publish, dismiss → archive). |
| A10 | `services/knowledge_synth.py:550` — archive proposal | Cron `knowledge_synth` (same tick) | InboxItem `improvement` | **ACTION** | None (`source_table='bucket_articles'` + `source_id=<target article>`) | Knowledge pipeline proposes archiving a stale article; operator decision. |

## Bucket B — shared plumbing (do **not** flip; becomes the Inbox channel impl)

| # | Site | Caller | Why it stays |
|---|------|--------|--------------|
| B1 | `services/inbox/intake.py:303` — `_build_inbox_item` | Findings-intake service (generic constructor: type/category/priority/headline/auto-resolve policy) | This *is* the canonical InboxItem factory. The Phase-1 `InboxChannel` implementation of `notify()` should delegate here (or to a thin wrapper) so all rows keep identical shape. Flipping it onto itself would be circular. |
| B2 | `services/agent/tools.py:2521` — Navigator `inbox_create` agent tool | Chat surface (Navigator tool call) | Agent-initiated letters are a *conversational* action, not engine egress — the Navigator decides to write to the operator. It can adopt `notify()` later as a consumer, but it is not part of the engine seam flip. |

## Bucket C — console render path / out of scope (dies or stays with the console)

| # | Site | Caller | Why out of scope |
|---|------|--------|------------------|
| C1 | `api/v1/routes/dashboard.py:481` — `_mirror_stuck_prs_to_inbox` (def :404) | Console dashboard GET render side-effect | Created only when someone loads the dashboard; a render-time mirror, not engine egress. Slated for the Phase-4 console strangler (dashboard render routes are DUPLICATE). If stuck-PR detection is worth keeping headless, it must be re-homed onto a cron + `notify()` — tracked under Phase-4 classification (ELS-220), not the seam. |
| C2 | `api/v1/routes/agent_runs.py:2572` — manual `POST /inbox` create endpoint | Explicit API request (agent/manual letter creation) | A request-scoped CRUD endpoint: the caller already chose the Inbox as the destination. Not an engine decision point — nothing to route. |

## Engine Linear-comment egress (reference impls for `LinearCommentChannel`)

These already post Linear comments from the engine side and prove the
`tracker_adapter.comment()` path works in production. The Phase-1
`LinearCommentChannel` should follow their shape (resolve binding → `gateway.comment(ref, body=…)`).

| Site | What it does |
|------|--------------|
| `services/inbox/action_executors.py:112` | Operator-cancel executor: transitions the ticket to `Canceled` and posts the operator's optional comment via `resolved.gateway.comment(...)`. |
| `services/clarifications_sync.py:642` | Posts the clarification answer back to the source ticket via `binding.gateway.comment(ticket, body=comment_body)`. |
| `api/v1/routes/agent_runs.py:4410` (context of A5) | Finish handler posts the agent's comment to the ticket before mirroring to Inbox — the same two-channel pattern `notify()` formalizes. |

## Counts

- Bucket A: **10** sites (the engine seam scope for ELS-223/224/225)
- Bucket B: **2** sites (channel plumbing — preserved)
- Bucket C: **2** sites (console-path — Phase 4 territory)
- Total `InboxItem(` construction sites in app code: **14** ✓

## Flip assignment (Phase 1)

- ELS-223 (dispatcher + self-heal): A1, A7
- ELS-224 (agent_runs finish + pick-tick): A2, A3, A4, A5, A6
- ELS-225 (services): A8, A9, A10
