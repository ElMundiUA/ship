# Control plane vs. read-model — Linear-read inventory (ELS-227)

> Thesis 2 (headless-but-stateful): **domain** state (ticket lifecycle,
> PR/approval/CI) lives in Linear/GitHub; **control** state (lease, cap,
> cascade, idempotency) lives in Ship Postgres and may NEVER be derived
> from Linear labels/status. This doc enumerates every place Ship reads
> Linear state on the dispatch path and classifies it. The companion
> guard test is `apps/backend/tests/test_control_plane_invariant.py`.

## The four control primitives — Postgres-only (verified)

| Primitive | Where | Resolves against | Linear input |
|---|---|---|---|
| Lease | `dispatcher.acquire_lock` (:235) — atomic `INSERT … ON CONFLICT DO NOTHING` on `agent_dispatch_locks` | Postgres | none |
| Release / sweep | `dispatcher.release_lock` (:290), `sweep_expired_locks` (:433) | Postgres (`expires_at`) | none |
| Per-ws cap | `dispatcher.count_active_locks` (:466) | Postgres row count | none |
| Cascade budget | `dispatcher._count_recent_dispatches` (:517) — `COUNT(*)` on `audit_log` `action='agent_run.dispatch'` within `CASCADE_WINDOW_S` | Postgres | none (`ticket_ref` is an identifier key into audit_log, not Linear state) |

Call sites audited (all kwargs Postgres-shaped): dispatcher.py
:402 :858 :882 :920 :932 :1028 :1078 :1083 :1123 :1127 :1230 :1234
:1373 :1384 :1390 :1408 :1440 and `agent_runs.py:4676` (finish-path
release).

## Legitimate Linear reads on the dispatch path — DOMAIN (pickup-gating)

| Read | Where | Classification | Why it is domain, not control |
|---|---|---|---|
| Issue STATUS (workflow state) | `tracker_poller._poll_installation` (reads state, emits transition event) | **domain — the settled-correct sole transition trigger** (`tracker_fsm.py:277`) | The status field decides *what work exists*; the lease decides *who may run it*. The poller never touches locks. |
| `stage:*` labels | `tracker_poller._extract_fsm_stage` (:122); dispatcher stage→routine mapping (:62) | **domain — routing breadcrumb** | Picks WHICH routine handles the ticket; does not gate concurrency. |
| `planning:anchor` label | `dispatcher.maybe_dispatch` (`is_anchor`, :1066) | **domain — pickup/parallelism semantics** | Exempts the anchor from the project lock *acquisition decision*, but the lock itself is still acquired/checked purely in Postgres. The label never feeds `acquire_lock` arguments. |
| Freeze-overlay labels (`OVERLAY_FREEZE_LABEL_PREFIXES`, e.g. `blocked`) | dispatcher pickup filter | **domain — pickup gate** | A frozen ticket is *not eligible work* (domain); the lease table is untouched. Removing the label re-eligibilizes — it does not release any lock. |
| `needs_clarification` signal label | finish path (`add_signal_label`) | **domain — egress breadcrumb** | Write-only projection for the human; the clarification resolution flows back through the STATUS field / Inbox, not by reading this label into a lock decision. |
| Ticket snapshot labels (`get_ticket_snapshot`) | dispatcher :979 (`ticket_labels`) | **domain** | Used for `is_anchor` / routing above. |

## Violations found

**None.** Every Linear read on the dispatch path gates *pickup/routing*
(what + which), never *lease/cap/cascade* (whether/how-many). The
control primitives' signatures and all call sites carry only
Postgres-shaped arguments — now pinned by the guard test.

## Rules for future changes

1. A new parameter on `acquire_lock` / `count_active_locks` /
   `_count_recent_dispatches` / `release_lock` must be added to the
   guard test's `_ALLOWED_PARAMS` **deliberately** — if it is
   label/status/tracker-shaped, the change is architecturally wrong.
2. Projection (FSM → Linear status/labels) is **one-directional**
   (egress). Nothing may invert a projected label back into a control
   decision. (`TRACKER_MAPPING_HINTS` unification: ELS-228;
   StateProjector: ELS-229.)
3. New "is this ticket eligible?" reads of Linear are fine (domain);
   new "may I run / how many are running?" reads of Linear are not
   (control).
