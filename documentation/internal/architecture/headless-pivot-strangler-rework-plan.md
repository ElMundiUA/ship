# Headless Pivot — Strangler Rework

> Implementation plan generated 2026-06-11 via multi-agent planning workflows (analyze → synthesize → adversarial review), green-lit against the live codebase. Loaded into Ship Linear (team ELS).

## Overview

A strangler-fig in place behind feature flags (not a rewrite, not a new repo): 208 files keep importing backend.app.*, 86 migrations and the DB/CLI/secrets/CI stay shared, and the 48k-LOC test suite is the net for every in-place deletion. The plan is sequenced cheapest-and-most-reversible first and preserves the settled global order: config spine -> notification seam -> control guardrail -> scheduled trigger(c) -> console strangler -> superstructure(adapters/self-spawn/autonomy) -> local executor(a), then the two new workstreams as later phases: chat-edge/inbound (T4) and the deterministic workflow primitive (T8). The shape: a thin config-as-code spine lands first because notification routing, the console kill-switch, and the autonomy dial all read from it; the autonomy column migration is pulled FORWARD into Phase 0 (per the must-fix) so the local executor and autonomy gate are not serialized behind the entire console teardown. The notification seam is the single reversible egress interface, default inbox-only so every emit-site flip is an observable no-op until a workspace opts in; launch egress is inbox-only everywhere with Linear/email OPT-IN per workspace. Phase 2 pins the headless-but-stateful invariant (control state stays in Ship Postgres; STATUS is the only FSM transition signal) with an audit + guard test, unifies the runtime TRACKER_MAPPING_HINTS consumer plus the two hardcoded maps into one egress-only source of truth (corrected premise: it IS consulted at runtime, tracker_fsm.py:287), and rebuilds the deleted health/stall residue routing its stall signal through the Phase-1 seam. Phase 3 dogfoods scheduled ticket-creating routines over the shipped dispatch path. Phase 4 tears down the console leaf-first behind the suite, keeping the Inbox approval surface reachable in every console mode. Phase 5 codifies adapter discipline, adds the gated `ship` self-spawn provider forced through cascade+cap, and wires the autonomy dial at the FSM gate + agent-role prompts + auto-merger with CI-green pinned non-negotiable in ALL profiles and the control plane strictly off-limits to the dial. Phase 6 builds the net-new lease-free local executor (default-off everywhere, escalation suggested not forced). Phase 7 (chat-edge T4) makes human replies first-class conversational inbound without regressing the STATUS-only invariant, and hardens the Telegram control-adjacent surfaces (durable pending-action store, signed single-use callbacks, deep-link-not-commit approvals); it depends on the notification seam and the durable store. Phase 8 (workflow primitive T8) adds a deterministic bounded orchestrator distinct from the FSM, every leaf routed through a WorkflowDispatchGate that reuses lease/cap/cascade/idempotency; it depends on the Phase-5 self-spawn recursion guard. Founder decisions are baked into bodies: balanced backfill + balanced default for new workspaces, high is opt-in everywhere (even ElMundi), inbox-only launch egress, control plane never exempted by the dial, local executor default-off with E20-style suggestion, email_to from workspace.settings fail-closed, and all new migrations are 0086+ forward revisions off HEAD=0085.

## Project description

Convert Ship into a fully headless engine that acts THROUGH Linear+GitHub+email, executed as a strangler-fig in place behind feature flags (not a rewrite, not a repo lift-and-shift): 208 files keep importing backend.app.*, the DB/CLI/secrets/CI/migrations stay shared, and the 48k-LOC test suite is the net for every in-place deletion. Sequenced cheapest-and-most-reversible first: a config-as-code spine (channel routing + autonomy/console scopes) and the notification egress seam land early; the headless-but-stateful control-plane guardrail (control state stays in Ship Postgres, STATUS is the only FSM transition signal) is a cross-cutting invariant every phase respects; scheduled ticket-creating routines dogfood the shipped dispatch path; the console frontend + console-only API routes are deleted leaf-first behind the suite; superstructure (agent adapters, gated ship self-spawn, per-workspace autonomy dial) and the net-new lease-free local executor land next; then chat-edge inbound (human replies as conversational inbound without regressing the STATUS-only invariant) and the deterministic workflow primitive (a bounded orchestrator distinct from the FSM, every leaf routed through the control plane). Founder defaults baked in: balanced backfill + balanced default for new workspaces, high is opt-in everywhere (even ElMundi), inbox-only launch egress (Linear/email opt-in per workspace), control plane strictly off-limits to the autonomy dial, local executor default-off + suggest-escalation, email_to from workspace.settings fail-closed, and all new migrations are 0086+ forward revisions off HEAD=0085. Honors all eight settled theses; the test suite is the safety net for every deletion.

## Phases & gates

### Phase 0 — Config-as-code spine + inventories

**Goal:** Land the shared config surface every later phase reads from, the two authoritative ledgers (emit-sites, console routes) that drive the strangler, and the autonomy column (pulled forward) — all zero-runtime-risk.

**Exit gate:** config scopes round-trip via GET/PUT and .ship/config.yml; both ledgers checked in and reviewer-verifiable by re-running the grep; autonomy column migrates+downgrades cleanly with balanced backfill; no behavior change.

### Phase 1 — Notification seam (reversible egress)

**Goal:** Introduce the single notify(workspace,ticket,body,level) interface routing across Inbox/Linear/email, flip all engine emit-sites onto it behind a default-off flag (inbox-only launch egress), and prove a real sandbox email send end-to-end.

**Exit gate:** SHIP_NOTIFY_CHANNELS=false forces inbox-only globally and all flipped sites produce byte-identical InboxItems vs today; rg 'InboxItem(' shows zero bucket-A sites remaining; audit_log records per-channel outcomes; a real sandbox email send is exercised end-to-end at least once.

### Phase 2 — Control-plane guardrail + health residue

**Goal:** Pin the thesis-2 invariant (control state stays in Postgres; STATUS is the only transition signal) with an audit+guard-test, unify the runtime TRACKER_MAPPING_HINTS consumer plus the two hardcoded maps into one egress-only source of truth, and rebuild the deleted is-alive/where-stuck surface routing its stall signal through the Phase-1 seam.

**Exit gate:** guard test fails if any lock/cap/cascade primitive gains a Linear-label input; the unified mapping table targets the same native states as before (no drift); engine-health computes purely from agent_dispatch_locks+audit_log (zero writes asserted); stall-notify dedupes per (ticket,reason) and provably never touches a lock or transition.

### Phase 3 — Scheduled ticket-creating routines (trigger c)

**Goal:** Add the net-new (c) flavor: a cron that creates a Linear ticket in a target FSM stage so the already-shipped dispatch path executes it, idempotency-guarded against open duplicates.

**Exit gate:** two ticks in one period_key create at most one ticket (durable audit_log dedup, survives failover); created ticket is picked up by existing tracker_poller->dispatcher with no new dispatch code; per-workspace failures isolated; egress-off/disabled workspaces skipped fail-closed.

### Phase 4 — Console strangler / teardown

**Goal:** Flag the console dark per-workspace, migrate mutable settings to config-as-code, then delete DUPLICATE routes leaf-first (pages->BFF->backend) behind the test suite, collapsing the frontend to a residual control panel while keeping the Inbox approval surface reachable in every mode.

**Exit gate:** OpenAPI diff shows ONLY console-render routes removed, zero EXCLUDED routes touched; dashboard_priorities.py retained (control-plane-adjacent); full build+typecheck+Playwright+backend suite green; dispatch+install+webhook+Inbox+config e2e green; Inbox approval surface reachable in residual AND off modes.

### Phase 5 — Superstructure: adapters, self-spawn, autonomy dial

**Goal:** Codify adapter discipline (thesis 5), add the gated `ship` self-spawn provider forced through cascade+cap (thesis 6), and wire the autonomy dial at the FSM gate + agent-role prompt + auto-merger (thesis 7) with CI-green non-negotiable and the control plane off-limits to the dial.

**Exit gate:** self-spawn 4-deep loop terminates via CASCADE_BLOCKED (test); CI-green invariant holds across all three autonomy profiles; high-autonomy-without-knowledge emits an audited warning/downgrade; adapter lint fails on any tool-native config write; no profile branch touches any lock/cap/cascade constant.

### Phase 6 — Local synchronous executor (trigger a)

**Goal:** Build the net-new lease-free, scratch-checkout, stop-before-push local executor as a distinct command, default-off everywhere, classifier-gated, escalating a->b only via create_issue (suggest, never hard-block).

**Exit gate:** local command never calls /tracker/next, /agent-runs/finish, pushBranch, openPullRequest, or the dispatcher (grep + e2e leaving origin untouched); ESCALATE creates a ticket through the existing create_ticket adapter with no lease handoff; default-off everywhere including ElMundi; misclassified big-feature turn SUGGESTS escalation, never blocks.

### Phase 7 — Chat-edge / inbound control (trigger a inbound, thesis 4)

**Goal:** Make human replies (Linear comments + Telegram) first-class conversational inbound to the Navigator WITHOUT regressing the STATUS-only transition invariant, and harden the Telegram control-adjacent surfaces (durable pending-action store, signed single-use callbacks, deep-link-not-commit approvals). Control plane never commits through chat.

**Exit gate:** comment ingestion adds exactly one Navigator turn per new human comment, calls no transition()/state write (asserted), and is idempotent; agent-vs-human classified by Linear author identity with marker fallback; keyboards survive a leader-failover via the durable store and never pollute the Console Inbox; callbacks are signed + single-use; actor-sensitive approvals render as deep-links, never commit from chat.

### Phase 8 — Deterministic workflow primitive (thesis 8)

**Goal:** Add a scripted bounded multi-agent orchestrator Ship INVOKES for one job and that COMPLETES — distinct from the reactive FSM — with every leaf routed through a WorkflowDispatchGate that reuses lease/cap/cascade/idempotency, fireable from all three triggers, complementary to (never merged with) the /process editor.

**Exit gate:** loader rejects fork-bomb specs (fanout/depth) BEFORE any dispatch; runtime is structurally incapable of spawning except through the gate (AST/grep check); a workflow recursion is refused at CASCADE_LIMIT=3 reusing _count_recent_dispatches; (workflow_run_id,step_id,attempt) UNIQUE enforces idempotency; control plane not loosened by autonomy profile (no profile branch in gate.py); processes.py unmodified (diff check).

## Critical path

1. Inventory and tag every engine emit-site (notification seam audit)
2. Add per-workspace notification channel-routing config + global kill-switch
3. Define notify() interface + Inbox/Linear/email channel router
4. Flip dispatcher + self-heal engine emit-sites onto notify()
5. Flip agent_runs finish + pick-tick engine emit-sites onto notify()
6. Wire notify() audit logging + sandbox email-transport check + rollback runbook
7. Audit + guard test: prove no control state is read from Linear labels/status
8. Add control-plane health/stall residue surface (is-alive / where-stuck)
9. Stall-notification: emit engine-stalled signal through the notify() seam
10. Classify every console page + backend render route (pre-tag dashboard_priorities.py EXCLUDED)
11. Add SHIP_CONSOLE_MODE per-workspace flag gating the authed shell (Inbox reachable in every mode)
12. Delete DUPLICATE analytics + dashboard render pages and their BFF proxies
13. Delete orphaned dashboard/analytics/repo_home backend render routes (retain dashboard_priorities.py)
14. Collapse root dashboard + repo pages to the residual status surface
15. Strangler regression gate: headless egress + Inbox + CLI boundary survive teardown
16. Add 'ship' self-spawn provider adapter + wire into runAgent behind a flag
17. Force self-spawn recursion THROUGH cascade-depth + per-ws cap (recursion guard)
18. W8.2 — WorkflowDispatchGate: route EVERY workflow-spawned agent through the control plane
19. W8.3 — Workflow runtime executor: schedule the DAG, drive leaves through the gate, collect structured outputs
20. W8.5 — Ticket/gate (b) + cron (c) triggers: fire a workflow from an FSM gate and a nightly cron
21. W8.8 — First dogfood workflows: multi-axis PR review+verify and nightly codebase audit, + /process boundary doc

## Top risks

- agent_runs.py is the busiest hand-rolled emit surface with subtle per-window audit dedup (tracker_outage short-circuits subsequent picks in the hour) AND is touched by three workstreams: Phase 1 emit-site flips (:772/:1689/:4023/:4433/:4523), Phase 2 StateProjector (transition_ticket :2421), and Phase 5 autonomy gate (finish_agent_run). Keep the audit-row dedup in the caller, route only the actual emit, and sequence/rebase the StateProjector and autonomy-gate edits deliberately since they mutate overlapping transition paths.
- Console teardown mis-tagging a headless-critical route as DUPLICATE breaks egress. dashboard_priorities.py is now PRE-TAGGED EXCLUDED (control-plane-adjacent: its WorkspaceProjectPriority model feeds project_state_sync, project_completion, and the Navigator picker tools.py:2402) — do NOT delete it. analytics_dora.py/dashboard.py may feed the daily_scheduler bundle summary; the verify-callers gate and the Phase-4 regression gate are load-bearing.
- Self-spawn fork-bomb: if the 'ship' provider (Phase 5) or a workflow leaf (Phase 8) reuses a trigger_kind with the is_cascade project-lock carve-out (dispatcher.py:1072) or any cap exemption, the project_lock-leak / fork-bomb class reopens. Both the self-spawn recursion guard and the WorkflowDispatchGate must make every spawn a first-class counted entrant; tests must assert termination at depth 3, not just the happy path. Keep both behind hard-off flags until their guard lands in the same PR.
- Autonomy 'high' loosening the code_review->auto_merge approval check is the highest-blast-radius change — a bug letting high also skip CI would autonomously merge red code. CI-green is pinned non-negotiable across all three profiles (the gate at agent_runs.py separates has_approval from the CI check, verified separable), and high requires a populated knowledge surface (audited downgrade) so high-autonomy never runs with broad rights and no guardrails. The control plane (cap/cascade/lease) is strictly off-limits to the dial — no exemption even for ElMundi.
- Chat-edge regressing the STATUS-only transition invariant (thesis 2): a naive Linear-comment-inbound that re-fires a stage would turn comments into a control channel. Phase 7 Task 'comment round-trip' must call no transition()/state write (test-asserted) and must model the clarification path's restraint; the agent-vs-human author disambiguation must be bulletproof or the agent-comment->poller->Navigator->agent-comment feedback loop reopens. Telegram callbacks must be signed + single-use and the durable store must not pollute the Console Inbox.
- Workflow runtime correlating async CI coding-leaf finishes back to in-memory state across replicas. Keep ALL runtime state in workflow_step_runs (DB), drive coding-leaf completion off the existing agent-runs/finish webhook + a reconcile tick rather than an in-process await; reasoning leaves stay synchronous in-process. A nightly workflow fan-out across many workspaces could stampede the dispatch cap — use a separate workflow:* prefix accounting (cap=1 like WORKSPACE_BUNDLE_CAP initially) so workflows cannot starve SDLC ticket dispatch.
- Local executor accidentally inheriting run.mjs's finish/PR side effects (renderExitProtocol / push helpers). The trimmed-prompt refactor must land first and the new command must not import the push/PR/finish helpers at all; refactoring the shared renderPrompt risks regressing the load-bearing (b) dispatch path — guard with existing run.mjs prompt tests before/after. Local executor is default-off everywhere and only ever SUGGESTS escalation.

## Open questions (attached to Phase 7/8 tickets)

- Durable pending-action store shape (Phase 7): reuse inbox_items with type='chat_action' (founder-leaning, cheaper, but pollutes the Console Inbox surface and overloads the one real rebuilt tool) vs a dedicated telegram_pending_action table (clean isolation, one extra 0086+ migration). Recommend the dedicated table if Console-filter airtightness is doubtful.
- For Linear-comment inbound (Phase 7): should the Navigator turn's reply be posted BACK as a Linear comment (visible loop-closure for the operator) or only delivered to the operator's notification channel? Posting back risks an agent-comment->poller->agent feedback loop unless author-exclusion is bulletproof; needs a founder call on whether the chat-edge reply is visible on the ticket.
- Canonical Linear identity of 'the workspace agent' for author disambiguation — the PAT user, the OAuth app/bot actor, or both? The poller comments(first:20) GraphQL block needs the right field (user.isMe vs botActor) and the answer differs per how each workspace's Linear integration is provisioned.
- Should Linear-comment inbound and Telegram inbound be unified behind one 'conversational inbound' abstraction now, or kept as two adapters? The shared piece is 'append a user turn to a Navigator thread, classify_shift=False, non-transitional'; consolidating early avoids drift but adds an abstraction before the second consumer is proven.
- Telegram 64-byte callback_data budget (Phase 7): confirm the compact token format (opaque store-id + truncated HMAC) gives acceptable collision/forgery resistance, or whether the full nonce must live only in the durable store with the button carrying just the row id.
- Workflow dispatch cap (Phase 8): should workflows get their OWN cap (SHIP_DEFAULT_WORKFLOW_DISPATCH_CAP) separate from both the SDLC ticket cap and WORKSPACE_BUNDLE_CAP, or reuse the bundle cap=1 accounting? Affects how much parallelism a single workflow can claim.
- Where should reasoning leaves execute at scale (Phase 8) — in the backend API process (reuse _run_subagent_loop synchronously, simple but ties up a request worker) or dispatched to a CI runner like coding leaves? v1 assumes reasoning=in-process; high-fanout nightly audits may need to move them to the CI/dispatch path.
- Is the /process editor expected to eventually REFERENCE workflows (a process stage whose action is 'run workflow X') as a sanctioned one-way integration point, or must the two stay fully decoupled? The thesis says complementary/no-merge; confirm whether a one-way 'process stage invokes workflow' edge is allowed.
- Autonomy-dial interaction with workflows and the 'ship' self-spawn provider: control plane stays fixed across profiles, but workflow AUTHORING/INVOCATION rights (who can add .ship/workflows/* and fire them from chat) likely vary by profile — needs a policy decision. Recommend high-only/internal-dogfood for the ship provider and for chat-triggered workflow launches at launch.
- Console.surface granularity: per-workspace (matches the autonomy dial) or per-user (operators keep full console for support, customers get residual)? And does the residual surface keep a minimal audit VIEW for oncall, or is shipctl/Linear activity sufficient?

## Issues by phase

### Phase 0 — Config-as-code spine + inventories

#### [S] Inventory and tag every engine emit-site (notification seam audit)
Produce the authoritative emit-site ledger the notification seam strangles. The 14 direct `InboxItem(` constructions in app code (verified via `rg -n 'InboxItem(' apps/backend/app --type py | grep -v test`) split into THREE buckets:

(A) ENGINE HEADLESS EGRESS (in-scope, flip to notify): dispatcher.py:741 `_file_no_target_repo_letter`; agent_runs.py:772 (tracker_outage blocker, pick-tick), :1689 (orphan_skipped improvement), :4023 (code_review->auto_merge gate-fail blocker), :4433 (needs_clarification mirror), :4523 (blocked mirror); runs.py:572 (self_heal_failed blocker); sdlc_readiness.py:354 (SDLC setup improvement); knowledge_synth.py:384 + :550 (knowledge draft improvements).
(B) SHARED PLUMBING (do NOT flip — becomes the Inbox channel impl): intake.py:303 `_build_inbox_item`; tools.py:2521 (agent-tool path).
(C) CONSOLE-ONLY / OUT-OF-SCOPE (dies with the console strangler): dashboard.py:481, agent_runs.py:2572 (POST /inbox manual-create endpoint).

Also tag the existing engine non-inbox Linear-comment egress as reference impls for LinearCommentChannel: inbox/action_executors.py operator-cancel comments, clarifications_sync.py `binding.gateway.comment`.

Deliver a markdown table under documentation/internal/architecture/ with columns: file:line, current channel, bucket(A/B/C), level (info/action/blocker), dedup key today, target notify level. No code change.

FOUNDER DEFAULTS: launch egress is inbox-only everywhere; every bucket-A target level is documented but routing stays inbox-only until a workspace opts in.

Acceptance criteria:
- Ledger lists all 14 InboxItem( app-code sites plus the engine Linear-comment sites, each tagged A/B/C with a one-line justification naming the caller (cron tick vs HTTP route handler).
- Every bucket-A row has a proposed notify() level and the dedup key it uses today (e.g. dispatcher 'no-target-repo:<ticket>').
- A reviewer can re-run the rg above and match every hit to a ledger row.

Key files: documentation/internal/architecture/notification-seam-emit-sites.md, apps/backend/app/services/dispatcher.py, apps/backend/app/api/v1/routes/agent_runs.py, apps/backend/app/api/v1/routes/runs.py, apps/backend/app/services/sdlc_readiness.py, apps/backend/app/services/knowledge_synth.py, apps/backend/app/services/inbox/intake.py

#### [M] Add console.surface + autonomy config scopes to config_registry
Extend the per-workspace ConfigScope registry (apps/backend/app/services/config_registry.py — today agent.provider, agent.default_profile, catalog.sources) with (a) `console.surface` (enum full|residual|off) and (b) `autonomy.profile` (enum high|balanced|conservative, thesis 7). These ride the existing GET/PUT /v1/workspaces/{ws}/config/{scope} surface (routes/config.py) which already does JSONSchema validation + admin gate + audit — no new route. Mirror both keys into the v2 file schema validator (packages/cli/lib/config/schema.mjs: add `console` and `autonomy` to KNOWN_TOP_LEVEL_V2 + validators) so they round-trip through .ship/config.yml.

This scope is the config spine three later phases consume (notification routing reads it indirectly; console kill-switch; autonomy dial). Adding it now without all consumers is inert but safe.

FOUNDER DEFAULTS: autonomy.profile default resolves to 'balanced' for any workspace without an explicit value; 'high' is opt-in (set explicitly per workspace, including ElMundi). console.surface defaults to 'full'.

Acceptance criteria:
- GET /v1/workspaces/{ws}/config lists console.surface and autonomy.profile with JSONSchema + current value.
- PUT rejects out-of-enum with the existing 422 invalid_value shape and writes an audit row.
- validateConfig() in schema.mjs accepts `console:` and `autonomy:` blocks and rejects bad enums; existing config-validation unit tests green.
- A workspace with no explicit value resolves autonomy.profile=balanced; ElMundi resolves high only when explicitly set.

Key files: apps/backend/app/services/config_registry.py, packages/cli/lib/config/schema.mjs, apps/backend/app/api/v1/routes/config.py

#### [M] Add per-workspace notification channel-routing config + global kill-switch
*depends on:* Inventory and tag every engine emit-site (notification seam audit)

Add the config surface notify()'s router reads, on the existing workspace.settings JSON column (tenancy.py Workspace.settings). Define a typed accessor `get_channel_routing(workspace) -> ChannelRouting` returning per-level channel lists with safe defaults INFO/ACTION/BLOCKER -> [inbox] (today's behavior: Inbox only, so the seam is a no-op until a workspace opts a level onto linear/email). Add a global kill-switch as a Settings field following the alias convention in core/config.py: `notification_channels_enabled: bool = Field(default=False, alias='SHIP_NOTIFY_CHANNELS')` — when False, notify() forces inbox-only regardless of per-ws config (instant global rollback). EmailChannel recipient lives at settings['notifications']['email_to'].

FOUNDER DEFAULTS: ALL workspaces launch inbox-only (notification channels default OFF); Linear/email egress is OPT-IN per workspace. No workspace ships with external egress on by default. Email recipient = workspace.settings['notifications']['email_to']; fail-closed skip when absent (no guessing).

Acceptance criteria:
- get_channel_routing on empty settings returns inbox-only for all three levels.
- SHIP_NOTIFY_CHANNELS unset/false makes the router return inbox-only even when a ws requests linear/email (test).
- A ws settings requesting {INFO:[inbox], BLOCKER:[inbox,linear,email]} with the flag on yields exactly those lists.
- Missing email_to yields a structured skip (not a guess); malformed settings fail closed to inbox-only with a logged warning, never raise. No DB migration (reuses settings JSON).

Key files: apps/backend/app/services/notify_config.py, apps/backend/app/core/config.py, apps/backend/app/db/models/tenancy.py, apps/backend/tests/test_notify_config.py

#### [L] Classify every console page + backend render route (pre-tag dashboard_priorities.py EXCLUDED)
Produce the authoritative per-route ledger driving every console deletion. Walk apps/console/src/app (authed + public pages) and the console-serving backend routes, tagging each.

DUPLICATE (delete — re-implements a Linear/GitHub view): analytics + analytics_dora.py + dashboard_live_system.py + dashboard.py render endpoints, audit render page (keep audit_log TABLE), deployments render page (NOT deploy.py engine), repos/[id] + repo_home.py, memory mirror, most of /r/[owner]/[repo].
HEADLESS-CANDIDATE (keep behind the console flag — no headless equivalent yet): settings/config editor, process FSM editor, knowledge import wizard + knowledge_import_sources.py, telegram bind + telegram.py, api-keys, members + invites, onboarding/login/invite.
RESIDUE: root dashboard->status, settings/danger, no-access, auth-error.
EXCLUDED (do NOT classify for deletion): inbox page + inbox.py, agent_runs.py, runs.py public_router, all *_oauth.py/*_webhook.py/github_app.py, config.py.

MUST-FIX — PRE-TAG dashboard_priorities.py EXCLUDED (control-plane-adjacent), NOT ambiguous/HEADLESS-CANDIDATE: its backing model WorkspaceProjectPriority is consumed by app/services/agent/project_state_sync.py (Backlog<->Todo projection), app/services/project_completion.py (sweep states), and app/services/agent/tools.py:2402 (Navigator picker/ordinal path). It MUST be retained.

CRITICAL: grep each remaining candidate's callers across packages/cli + app/integrations/telegram + app/services BEFORE tagging; default ambiguous routes to HEADLESS-CANDIDATE. Check whether daily_scheduler reads analytics_dora.py/dashboard.py for its bundle summary (then HEADLESS-CANDIDATE).

Acceptance criteria:
- Every page.tsx and every router.include_router target carries exactly one tag.
- Inbox store, agent_runs.py, runs.public_router, all oauth/webhook/github_app, config.py, AND dashboard_priorities.py tagged EXCLUDED with a reason.
- Each DUPLICATE names the Linear/GitHub view it duplicates.
- Ledger lists each route's frontend caller(s) so the deletion task can topo-sort.

Key files: apps/console/src/app, apps/backend/app/api/v1/router.py, dashboard.py, dashboard_priorities.py, analytics_dora.py, repo_home.py

#### [S] Add autonomy:{high|balanced|conservative} column to Workspace + migration 0086 (balanced backfill, pulled forward)
MUST-FIX SEQUENCING: pulled FORWARD from Phase 5 to Phase 0 so the local executor gate (Phase 6) and the autonomy FSM gate (Phase 5) are not serialized behind the entire console teardown. This is a trivial S migration with no dependency on the console work.

Per thesis 7, add the per-workspace autonomy profile. Add Workspace.autonomy (tenancy.py, alongside agent_provider at line 179 and max_concurrent_dispatches at line 185) as String(16) NOT NULL server_default 'balanced', with a CHECK constraint in ('high','balanced','conservative') in a NEW forward migration. HEAD is migration 0085 — this is 0086. This is the AGENT action-rights dial ONLY — it MUST NOT alter lease/cap/cascade/idempotency. Add resolve_autonomy_for_workspace(session, ws) -> Literal (extend agent_provider_resolver or a new module), default 'balanced'.

FOUNDER DECISIONS (baked in): backfill ALL existing rows to 'balanced' (the beta target); default for new workspaces is 'balanced'; 'high' is OPT-IN everywhere — do NOT seed ElMundi to high in this migration; ElMundi (and any high workspace) is opted in explicitly later via config. The control plane is strictly off-limits to this dial: no cap/cascade/lease constant or table changes.

Acceptance criteria:
- Workspace.autonomy exists with CHECK in the three values + server_default 'balanced'.
- Migration 0086 upgrades + downgrades cleanly; ALL existing rows backfill to 'balanced'.
- Resolver returns the ws autonomy with 'balanced' as the safe default for legacy/unknown.
- No change to any lock/cap/cascade constant or table; no row is seeded to 'high'.

Key files: apps/backend/app/db/models/tenancy.py, apps/backend/migrations/versions/0086_workspace_autonomy.py, apps/backend/app/services/agent_provider_resolver.py

### Phase 1 — Notification seam (reversible egress)

#### [L] Define notify() interface + Inbox/Linear/email channel router
*depends on:* Add per-workspace notification channel-routing config + global kill-switch, Inventory and tag every engine emit-site (notification seam audit)

Create app/services/notify.py exposing `async def notify(session, *, workspace_id, ticket_ref, title, body, level, dedup_key=None, payload=None, repo_id=None) -> NotifyResult`. `level` in {INFO, ACTION, BLOCKER}. Resolve per-workspace ChannelRouting (from the config task), then fan out to channel adapters behind `class Channel(Protocol): async def emit(self, ctx) -> ChannelResult`. Ship THREE impls:
1. InboxChannel — wraps the EXISTING intake._build_inbox_item (intake.py:303) so dedup/truncation/headline are identical to today (default for all levels). Map level->type (BLOCKER->'blocker', ACTION->'clarification', INFO->'improvement'); carry dedup_key into intake_handle.
2. LinearCommentChannel — resolve tracker via the same resolve_for_workspace path action_executors.py uses, then gateway.comment(TicketRef, body=...) (tracker_adapter.py); structured skip (no-op) when ticket_ref is None.
3. EmailChannel — recipient from settings['notifications']['email_to'] (fail-closed skip if absent, per founder decision), render via a new templates.render_notification_email, send via get_email_sender().send.

All channels swallow transport errors into ChannelResult(ok=False, detail=...) — notify() NEVER raises into the engine control path. Returns aggregate per-channel results. Interface + impls only; no emit-site flipped here.

Acceptance criteria:
- notify(level=INFO, default routing) produces an InboxItem byte-identical (type/title/summary/headline/intake_handle/payload) to calling _build_inbox_item directly (parametrized test).
- LinearCommentChannel posts via gateway.comment, ok=True; ticket_ref=None -> skip, posts nothing.
- EmailChannel renders+sends through a RecordingEmailSender; absent email_to -> structured skip; a transport exception is caught -> ChannelResult(ok=False), no propagation.
- A channel raising mid-fan-out does not stop the others; notify() never raises.

Key files: apps/backend/app/services/notify.py, apps/backend/app/services/email/templates.py, apps/backend/tests/test_services_notify.py

#### [M] Flip dispatcher + self-heal engine emit-sites onto notify()
*depends on:* Define notify() interface + Inbox/Linear/email channel router

Migrate the two lowest-risk pure-engine emit-sites, proving the seam end-to-end before the busier finish handler.
1. dispatcher.py:741 `_file_no_target_repo_letter` — replace direct InboxItem( with notify(..., level=ACTION, dedup_key=f'no-target-repo:{ticket_ref}', payload=<existing>). InboxChannel must reproduce the 'skip if open item with same intake_handle exists' dedup so behavior is unchanged under inbox-only.
2. runs.py:572 self_heal_failed — notify(..., level=BLOCKER, dedup based on existing source_table='self_heal_run'+source_id guard at runs.py:560-570). Keep the 'exists' dedup query in the caller OR move it into InboxChannel — pick one, be consistent. Support BOTH an intake_handle dedup_key AND a source_table/source_id dedup mode in InboxChannel.

Launch egress stays inbox-only (default flag off), so both flips are observable no-ops until a workspace opts a level onto linear/email.

Acceptance criteria:
- Existing dispatcher + self-heal tests pass unchanged under inbox-only routing (same fields, same dedup, no dup row on re-tick).
- With BLOCKER routing including linear+email, a self-heal failure additionally posts a Linear comment and sends one email.
- No InboxItem( remains at dispatcher.py:741 or runs.py:572 (grep clean).

Key files: apps/backend/app/services/dispatcher.py, apps/backend/app/api/v1/routes/runs.py, apps/backend/tests/test_services_dispatcher.py, apps/backend/tests/test_routes_runs.py

#### [L] Flip agent_runs finish + pick-tick engine emit-sites onto notify()
*depends on:* Flip dispatcher + self-heal engine emit-sites onto notify()

Migrate the five engine-side InboxItem( sites in agent_runs.py (finish handler + pick-tick handler, both engine egress): :772 tracker_outage blocker (BLOCKER, dedup by existing same-window audit guard), :1689 orphan_skipped (INFO), :4023 code_review->auto_merge gate-fail (BLOCKER), :4433 needs_clarification mirror (ACTION — preserve the _try_ticket_snapshot enrichment via payload/body), :4523 blocked mirror (BLOCKER). DO NOT touch :2572 (POST manual inbox-create endpoint, bucket C).

Keep the per-window audit-row dedup checks in the CALLER (e.g. tracker_outage short-circuits subsequent picks in the hour); route only the actual emit through notify(). Thread ticket_ref so LinearCommentChannel can target the clarification/blocked mirrors when a workspace opts in.

COORDINATION NOTE: this file is also edited by the Phase-5 autonomy gate (finish_agent_run) and the Phase-2 StateProjector (transition_ticket :2421); hold the dedup-in-caller discipline and rebase deliberately.

Acceptance criteria:
- All five sites construct via notify(); :2572 untouched.
- Existing finish-handler + pick-tick tests pass unchanged under inbox-only (same rows, same dedup).
- needs_clarification carries the ticket snapshot in body/payload exactly as today.
- With ACTION/BLOCKER routing including linear, clarification + blocked additionally post a Linear comment on the correct ticket.

Key files: apps/backend/app/api/v1/routes/agent_runs.py, apps/backend/tests/test_v1_agent_runs.py

#### [M] Flip sdlc_readiness + knowledge_synth service emit-sites onto notify()
*depends on:* Define notify() interface + Inbox/Linear/email channel router

Migrate the remaining engine-side service emit-sites: sdlc_readiness.py:354 (SDLC-setup improvement letter, INFO/ACTION) and knowledge_synth.py:384 + :550 (knowledge draft publish-vs-archive improvements, ACTION). These run under cron (cron_jobs._knowledge_synth_tick), clean headless-egress candidates. Thread the existing derive_headline output through notify()/InboxChannel. Keep type='improvement' for both knowledge drafts so the downstream action_executors/side_effects resolver still matches.

Acceptance criteria:
- Both services emit via notify(); no InboxItem( at the three sites (grep clean).
- Existing sdlc_readiness + knowledge_synth tests pass under inbox-only with identical rows (including headline).
- The side-effect resolver still finds the knowledge-draft rows (asserted).

Key files: apps/backend/app/services/sdlc_readiness.py, apps/backend/app/services/knowledge_synth.py, apps/backend/tests/test_services_sdlc_readiness.py, apps/backend/tests/test_services_knowledge_synth.py

#### [M] Wire notify() audit logging + sandbox email-transport check + rollback runbook
*depends on:* Flip agent_runs finish + pick-tick engine emit-sites onto notify(), Flip sdlc_readiness + knowledge_synth service emit-sites onto notify()

Make the seam observable, reversible, and proven against a real transport. (1) Record every notify() invocation + per-channel ChannelResult to the existing audit_log with action='notify.emit', target_kind='notification', so an operator sees which channels fired/failed. Make the audit write best-effort (catch+log) so a failed audit insert never rolls back the emit — consistent with the existing 'audit failure must not sink the response' pattern in agent_runs.py. (2) SHOULD-FIX: exercise a REAL (sandbox) email send end-to-end at least once — do NOT rely solely on RecordingEmailSender; the bootstrap-intelligence memory note ('harvest never runs in prod') is a reminder that an assumed-wired prod transport can silently fail-closed, so BLOCKER->email for an opted-in workspace must be proven against the actual get_email_sender().send path in a sandbox profile. (3) Document the rollback: SHIP_NOTIFY_CHANNELS=false forces inbox-only globally (instant kill, zero code), and per-workspace settings can dial a single level back to inbox-only. (4) Write the operator runbook under documentation/internal/operations/.

Acceptance criteria:
- Each notify() call writes one audit_log row capturing level, requested channels, per-channel ok/skip/fail.
- A sandbox-profile test (or documented manual smoke) sends one real email through get_email_sender().send and asserts delivery, not just a recording.
- SHIP_NOTIFY_CHANNELS=false demonstrably suppresses all linear+email emits in a test while inbox still works.
- Runbook documents enable steps, the verification audit query, the sandbox email smoke, and both rollback levers.

Key files: apps/backend/app/services/notify.py, documentation/internal/operations/notification-seam-runbook.md, apps/backend/tests/test_services_notify.py

### Phase 2 — Control-plane guardrail + health residue

#### [M] Audit + guard test: prove no control state is read from Linear labels/status
Pin thesis-2's invariant. Inventory every place Ship READS Linear state for a control decision and classify legitimate (domain lifecycle) vs illegitimate (control masquerading as a label).

Legitimate today: tracker_poller._poll_installation reads issue state + stage:* labels to emit a transition event — the STATUS field as sole transition trigger is settled-correct (tracker_fsm.py:277).
Confirm the four control primitives are Postgres-only: AgentDispatchLock INSERT ON CONFLICT lease (agent_dispatch.py + dispatcher.acquire_lock dispatcher.py:235), two-level ticket:/project: lock (dispatcher.py:808/:1072), cascade budget via audit_log COUNT (_count_recent_dispatches, CASCADE_LIMIT=3 at dispatcher.py:148), per-ws cap via count_active_locks (dispatcher.py:466).
Scrutinize freeze-overlay labels (OVERLAY_FREEZE_LABEL_PREFIXES) and stage:*/planning:anchor breadcrumbs — assert these gate PICKUP (domain), not LEASE/CAP (control).

Land a guard test that greps the lock-primitive CALL SITES' arguments and fails if acquire_lock/count_active_locks/_count_recent_dispatches gain a Linear label/status input.

Acceptance criteria:
- Doc enumerates every Linear-read in the control path with file:line, labeled domain vs control.
- Confirmed: lease, two-level lock, cascade budget, per-ws cap, idempotency all resolve solely against Postgres, zero Linear reads.
- Guard test fails if a lock primitive gains a Linear input.
- Each at-risk label classified pickup-gating (domain) or flagged as a violation.

Key files: apps/backend/app/services/dispatcher.py, apps/backend/app/services/tracker_poller.py, apps/backend/app/db/models/agent_dispatch.py, apps/backend/tests/services/test_control_plane_invariant.py, documentation/internal/architecture/control-plane-vs-readmodel.md

#### [M] Unify TRACKER_MAPPING_HINTS runtime consumer + the two hardcoded maps into one egress-only source of truth
*depends on:* Audit + guard test: prove no control state is read from Linear labels/status

MUST-FIX — CORRECTED PREMISE: TRACKER_MAPPING_HINTS (tracker_fsm.py:154) is NOT doc-only. It IS consulted at runtime at tracker_fsm.py:287 (`mapping = TRACKER_MAPPING_HINTS.get(normalised or '')`) inside the seed-render code path. Do NOT 'promote' it from doc-only; instead UNIFY the existing runtime consumer with the two hardcoded maps so there is one egress-only source of truth, not three.

Make the table (or a typed sibling per tracker_kind) the single declarative WRITE map the projection layer uses to pick which native Linear/GitHub status to write for a given FSM state. Add a module docstring + assertion that it is consumed only on the egress (Ship->tracker) path and NEVER inverted to parse native status back into FSM state for a control decision. Reconcile the existing hardcoded mappings in linear tracker_adapter._fsm_to_linear_state (tracker_adapter.py:695) and project_state_sync._TRANSITION_PLAN against this table.

Acceptance criteria:
- The table is the single source consumed by the existing tracker_fsm.py:287 path AND the linear projection path.
- tracker_adapter._fsm_to_linear_state and project_state_sync._TRANSITION_PLAN derive from the shared table.
- Module docstring + test assert egress-only (no path inverts it to a lock/cap/cascade decision).
- A per-state equivalence test confirms the unified table targets the same native states as before (guard against drift between display strings and live state IDs).

Key files: apps/backend/app/services/tracker_fsm.py, apps/backend/app/integrations/linear/tracker_adapter.py, apps/backend/app/services/agent/project_state_sync.py, apps/backend/tests/services/test_tracker_fsm.py

#### [L] Consolidate FSM->tracker status writes into one write-only StateProjector (flagged)
*depends on:* Unify TRACKER_MAPPING_HINTS runtime consumer + the two hardcoded maps into one egress-only source of truth

Today FSM state projects onto Linear from >=3 scattered sites: tracker_adapter.transition() (workflow-state move + stage: breadcrumb), project_state_sync.sync_project_tickets_for_state (Backlog<->Todo), and agent_runs.transition_ticket route (agent_runs.py:2421). Introduce a single StateProjector seam (app/services/state_projector.py) all FSM->tracker projections funnel through, parameterised by the shared table from the prior task.

Constraint (thesis 2): ONE-DIRECTIONAL — writes the human read-model and returns success/failure, output NEVER consumed as control input; the stage:* breadcrumb stays the poller's pickup hint (domain), not a lease. Projection is best-effort + idempotent and decoupled from lock acquisition — a tracker 5xx must not release/acquire any Postgres lock. Strangler-fig behind SHIP_STATE_PROJECTOR_UNIFIED. Keep the human-only 'Done' authorization gate at the route layer, NOT in the projector.

COORDINATION NOTE: agent_runs.py:2421 is also touched by the Phase-1 emit-flips and the Phase-5 autonomy gate; sequence/rebase deliberately.

Acceptance criteria:
- All FSM->tracker status/label writes route through StateProjector when the flag is on; flag-off path byte-identical to today.
- Test asserts StateProjector never calls acquire_lock/release_lock/count_active_locks and its return is not consumed by a control decision.
- Simulated tracker 5xx returns an errored report and provably does not mutate agent_dispatch_locks.
- Re-projecting an already-projected state is idempotent (no dup labels, no churn).

Key files: apps/backend/app/services/state_projector.py, apps/backend/app/integrations/linear/tracker_adapter.py, apps/backend/app/services/agent/project_state_sync.py, apps/backend/app/api/v1/routes/agent_runs.py, apps/backend/app/core/config.py, apps/backend/tests/services/test_state_projector.py

#### [M] Add control-plane health/stall residue surface (is-alive / where-stuck)
*depends on:* Audit + guard test: prove no control state is read from Linear labels/status

Phase 2 of the rearchitecture deleted both self-heal crons + the scan_eligible_tickets backstop + runner-fail detectors. Net: the only health surface is /v1/health (DB ping only) — a stalled FSM goes silent. Per thesis 2 this is the ONE piece of state with no external home, so it lives in Ship. Build a READ-ONLY surface deriving stall/liveness purely from existing Postgres control state (no new authoritative store):
(a) leases past expires_at but not swept (agent_dispatch_locks -> sweeper not running);
(b) tickets with a project:/ticket: lock held > N minutes with no recent audit_log dispatch row (stuck-in-flight);
(c) last successful dispatch timestamp per workspace (audit_log action='agent_run.dispatch');
(d) reprovision drift count.
Expose GET /v1/workspaces/{ws}/engine-health returning StalledTicket[] + EngineLiveness. Read-only; computes on request; never dispatches or mutates locks. Thresholds config-driven, start conservative (avoid re-creating the deleted cron's false positives).

Acceptance criteria:
- Endpoint returns liveness (last-dispatch ts, expired-but-unswept lock count) + stuck tickets with lock-key, age, stall reason.
- All values from agent_dispatch_locks + audit_log only; endpoint mutates zero rows (asserted).
- A ticket holding a project: lock > threshold with no audit dispatch in window is reported with reason 'lock_held_no_progress'.
- Read-only and authorised per-workspace like other /v1/workspaces routes.

Key files: apps/backend/app/api/v1/routes/engine_health.py, apps/backend/app/services/engine_health.py, apps/backend/app/api/v1/schemas.py, apps/backend/tests/api/test_engine_health.py

#### [M] Stall-notification: emit engine-stalled signal through the notify() seam
*depends on:* Add control-plane health/stall residue surface (is-alive / where-stuck), Wire notify() audit logging + sandbox email-transport check + rollback runbook

Close the silent-failure gap: when engine_health detects a stalled ticket or dead engine (no dispatch > threshold, or sweeper not running), emit a structured failure SIGNAL through the Phase-1 notify() seam (level=BLOCKER) — do NOT build a new transport. The engine_health task defines the stall payload (workspace, ticket_ref, lock_key, age, reason); notify() handles channels. Drive from a lightweight scheduled check (reuse daily_scheduler / cron surface, NOT a resurrected self-heal cron that mutates state) that calls engine_health and forwards anything stuck past threshold, behind SHIP_STALL_NOTIFY (default off).

Idempotency: never re-notify the same (ticket_ref, reason) within a cooldown window — dedupe via audit_log action='engine.stall_notified' (cascade-count pattern). The notification is context-only: MUST NOT move ticket status or touch a lock.

Acceptance criteria:
- The scheduled check forwards stuck tickets from engine_health to notify() behind SHIP_STALL_NOTIFY.
- Re-running does not re-notify the same (ticket_ref, reason) inside the cooldown (deduped via audit_log); scheduler interval >= cooldown.
- The notifier provably never calls transition/acquire_lock/release_lock (test).
- Payload schema matches the notify() interface so it swaps cleanly.

Key files: apps/backend/app/services/engine_health.py, apps/backend/app/services/stall_notifier.py, apps/backend/app/services/daily_scheduler.py, apps/backend/app/core/config.py, apps/backend/tests/services/test_stall_notifier.py

### Phase 3 — Scheduled ticket-creating routines (trigger c)

#### [S] Add open-ticket idempotency guard for scheduled-routine ticket creation
The 'don't dup an open one' requirement. Before creating a routine ticket, check whether a ticket for the same (workspace, routine_kind, period) already exists and skip if so. Implement via the audit_log-as-ledger pattern the codebase already uses for cascade depth: write a `scheduled_routine.ticket_created` audit row carrying {routine_kind, period_key, ticket_ref} on creation, and gate creation on a COUNT of that action for the current period_key being zero. This avoids a tracker round-trip per tick and survives leader failover (audit_log is durable). period_key derived from cadence (UTC date for daily, ISO week for weekly).

Acceptance criteria:
- Two ticks within the same period_key create at most one ticket (second logs a skip).
- The guard reads from audit_log (durable), holds across replica/leader failover.
- A new period creates a fresh ticket — covered by a unit test against a mocked audit_log.
- period_key granularity asserted to match the registered cron_expr cadence.

Key files: apps/backend/app/services/scheduled_routines.py, apps/backend/tests/services/test_scheduled_routines.py

#### [M] Add ticket-creating scheduled-routine mechanism (cron -> Linear ticket in FSM stage)
*depends on:* Add open-ticket idempotency guard for scheduled-routine ticket creation

Net-new (c) flavor. Today daily_scheduler.py only fires WORKSPACE-SCOPE bundles (maybe_dispatch_workspace_bundle, no ticket). Add a second shape that CREATES a Linear ticket in a target FSM stage so the already-shipped ticket->tracker_poller->dispatcher->run.mjs path executes it. Build services/scheduled_routines.py with a registry of specs {kind, cron_expr, target_fsm_stage, title_template, body_template, dedup_key}. Each tick: for every workspace with a ready Linear install (reuse the NativeIntegrationInstallation query from daily_scheduler, provider=='linear', status=='ready'), call the tracker create_ticket adapter (same path _tool_ticket_create uses) to open one ticket in target_fsm_stage. Wire ticks via register_cron + cron_with_lock with NEW CronLockId entries.

GROUNDING NOTE: CronLockId max in use is now 1024 (DEPLOYMENTS_RECONCILE) — reserve fresh ids from 1025+ (do NOT reuse any retired number). register via a register_all delegated like daily_scheduler.register_all.

FOUNDER DEFAULT: egress-off / disabled workspaces are skipped fail-closed.

Acceptance criteria:
- A new CronLockId reserved per routine kind (from 1025+, no number reuse).
- On tick, exactly one Linear ticket created per eligible workspace in the configured target_fsm_stage (gated by the idempotency guard).
- The ticket is picked up by the existing tracker_poller->dispatcher path with no new dispatch code.
- Per-workspace failures isolated (one bad install does not abort the tick), matching daily_scheduler's try/except-per-install.

Key files: apps/backend/app/services/scheduled_routines.py, apps/backend/app/services/cron.py, apps/backend/app/services/cron_jobs.py, apps/backend/app/services/daily_scheduler.py

#### [S] Document scheduled-routine title/body templates in agent_roles
*depends on:* Add ticket-creating scheduled-routine mechanism (cron -> Linear ticket in FSM stage)

The new ticket-creating routines are the INVERSE of workspace-scope bundles: the cron creates a ticket and the NORMAL SDLC stage agents (intake/BA/dev) process it — so they need no new bundle role. What is net-new is the title/body TEMPLATE the cron writes so the downstream stage agent gets a well-shaped, self-contained brief. Add templates as data in scheduled_routines.py and a short doc block (or a thin agent_roles/*.md if a routine wants a custom downstream prompt) per routine kind (daily/retro/techdebt), its target stage, and body template. Mirror the 'use Ship's API, never reach into Linear via MCP' guardrail from daily-digest.md.

Acceptance criteria:
- Each routine kind has a title_template + body_template rendering a self-contained brief (no reliance on the cron run's transient context).
- Templates do not instruct the downstream agent to bypass the FSM (no direct merge / no skipping stages).
- A routine declaring a custom role resolves via the existing GET /agent-roles/{slug}/resolve path with no new resolution code.

Key files: apps/backend/app/services/scheduled_routines.py, apps/backend/app/resources/agent_roles/scheduled-routine-techdebt.md

### Phase 4 — Console strangler / teardown

#### [M] Add SHIP_CONSOLE_MODE per-workspace flag gating the authed shell (Inbox reachable in every mode)
*depends on:* Add console.surface + autonomy config scopes to config_registry, Classify every console page + backend render route (pre-tag dashboard_priorities.py EXCLUDED)

Console-wide kill switch so the whole authed surface can go dark per-workspace without deleting code yet (strangler step 0, fully reversible). Today (authed)/layout.tsx only checks isApiConfigured() + session. Add a `console_mode` resolution (env default SHIP_CONSOLE_MODE=full|residual|off plus a per-workspace override from the console.surface config scope) consumed in the authed layout and app-shell.tsx NAV array. In `residual`: render only Dashboard(status) + Settings + Inbox, 302 every other authed path to a residual status page. Flag lives in config-as-code, not a Linear label.

MUST-FIX (don't orphan operator approvals): the Inbox approval surface (actor-sensitive approvals live in the Inbox UI, thesis 4) MUST stay reachable in EVERY mode. In `off` mode, do NOT 302 the whole authed tree blindly — keep the Inbox route (and its approval items) reachable, OR explicitly document that 'off' is ElMundi-only (high autonomy, no operator approvals expected) and assert no workspace with pending approval items can be set to 'off'. Default to keeping Inbox reachable.

Acceptance criteria:
- SHIP_CONSOLE_MODE=off serves the residual health page for non-Inbox authed routes but the Inbox approval surface remains reachable and functional, 200 on status, no crashes.
- SHIP_CONSOLE_MODE=full preserves today's behavior byte-for-byte (snapshot/e2e nav test green).
- Per-workspace override beats the env default, read via console.surface scope.
- A test asserts a pending operator approval is reachable under residual AND off modes.

Key files: apps/console/src/app/(authed)/layout.tsx, apps/console/src/components/app-shell.tsx, apps/console/src/lib/api/client.ts

#### [M] Migrate settings/general + workspace + agent settings to config-as-code keys
*depends on:* Add console.surface + autonomy config scopes to config_registry

Route every truly-declarative mutable knob through the config scope PUT instead of bespoke BFF endpoints. agent default/provider -> keep on agent.provider scope; registries/catalog -> catalog.sources scope; add the autonomy.profile + console.surface controls to the Config tab UI driven off the auto-rendered scope form (config.py GET returns JSONSchema so the renderer needs no per-key code). Retire apps/console/src/app/api/settings/{default-agent,config}/route.ts in favor of /api/proxy/v1/.../config. The Config tab becomes the single settings surface; general/workspaces collapse to read-only identity + the scope form.

SCOPE LIMIT: agent_roles, members/invites, repo_secrets, telegram bind are NOT pure config (GitHub installs/secrets/OAuth side effects) — they stay as dedicated routes. This task touches only declarative knobs.

Acceptance criteria:
- Every settings write previously hitting /api/settings/* now goes through GET/PUT /v1/workspaces/{ws}/config/{scope}.
- The Config tab auto-renders autonomy.profile + console.surface + agent.provider + catalog.sources from JSONSchema with no per-scope FE code.
- Deleted /api/settings/* BFF routes have no remaining importers (grep clean).
- Settings e2e (save agent provider, save autonomy) green.

Key files: apps/console/src/app/(authed)/settings/_shell/settings-shell.tsx, apps/console/src/app/api/settings/default-agent/route.ts, apps/console/src/app/api/settings/config/route.ts, apps/console/src/app/(authed)/settings/config/page.tsx

#### [M] Delete DUPLICATE analytics + dashboard render pages and their BFF proxies
*depends on:* Classify every console page + backend render route (pre-tag dashboard_priorities.py EXCLUDED), Add SHIP_CONSOLE_MODE per-workspace flag gating the authed shell (Inbox reachable in every mode)

First leaf-level deletion wave, gated behind the console.surface flag. Remove apps/console/src/app/(authed)/analytics/page.tsx, the (authed)/audit render page (keep audit_log table + audit.py write/ingest), the (authed)/deployments render page (NOT deploy.py engine), and their apps/console/src/app/api/dashboard/* + api/* proxy routes — all tagged DUPLICATE in the ledger (Linear cycles/insights + GitHub Insights already render velocity/DORA). Delete pages first, then the BFF proxies they called, leaving backend render endpoints orphaned for the next wave. The e2e suite is the net for cross-page imports.

Acceptance criteria:
- Deleted pages 404 (or redirect via residual mode) and are removed from app-shell NAV.
- No remaining import of the deleted BFF proxy modules in apps/console (grep clean).
- Full build + typecheck + Playwright suite green.
- audit_log table + audit.py ingest untouched; deploy.py (deployment ENGINE) untouched — only the deployments render PAGE removed.

Key files: apps/console/src/app/(authed)/analytics/page.tsx, apps/console/src/app/(authed)/audit/page.tsx, apps/console/src/app/api/dashboard, apps/console/src/app/(authed)/deployments/page.tsx

#### [M] Delete orphaned dashboard/analytics/repo_home backend render routes (retain dashboard_priorities.py)
*depends on:* Delete DUPLICATE analytics + dashboard render pages and their BFF proxies

Second wave: now the FE pages + BFF proxies are gone, remove the orphaned console-only render endpoints from apps/backend/app/api/v1/router.py and their modules — dashboard.py, dashboard_live_system.py, analytics_dora.py, repo_home.py — ONLY routes with zero non-console callers per the ledger. Re-run the caller grep across packages/cli, app/integrations/telegram, app/services FIRST; any endpoint a routine or the bot consumes downgrades to HEADLESS-CANDIDATE and is kept. Delete include_router lines + modules together so the route table shrinks atomically.

MUST-FIX: dashboard_priorities.py is PRE-TAGGED EXCLUDED (control-plane-adjacent) — its WorkspaceProjectPriority model feeds project_state_sync, project_completion, and the Navigator picker (tools.py:2402). It MUST be retained; do NOT delete its module or routes in this wave.

Acceptance criteria:
- Removed modules have no importer in apps/backend, packages/cli, or app/integrations (grep clean).
- dashboard_priorities.py retained and asserted still imported by project_state_sync/project_completion/tools.py.
- router.py include_router lines for deleted modules removed; app boots + OpenAPI regenerates.
- Backend test suite green; no route referenced by agent_runs.py/runs.py/webhooks removed.

Key files: apps/backend/app/api/v1/router.py, apps/backend/app/api/v1/routes/dashboard.py, dashboard_live_system.py, analytics_dora.py, repo_home.py

#### [M] Collapse root dashboard + repo pages to the residual status surface
*depends on:* Delete orphaned dashboard/analytics/repo_home backend render routes (retain dashboard_priorities.py), Migrate settings/general + workspace + agent settings to config-as-code keys

Define the irreducible RESIDUE: a single authed status page (replacing the rich page.tsx WorkspaceHome + /r/[owner]/[repo] repo dashboards) showing only (a) is-Ship-connected/healthy, (b) deep links to Linear project + GitHub repo (not mirrors), (c) the Inbox + Settings/Config (kept), (d) autonomy.profile + console.surface current values. This is the headless end-state of the frontend: a thin control-panel + deep-link launcher, all domain views living in Linear/GitHub. Keep login/onboarding/invite/no-access/auth-error as-is. Remove heavy data-fetching imports (getOpsDashboard, getPriorities, getLiveSystem) from page.tsx once their endpoints are gone.

Acceptance criteria:
- Root authed page renders status + deep links + Inbox/Settings links only, no mirrored ticket/PR/CI tables.
- page.tsx no longer imports getOpsDashboard/getPriorities/getLiveSystem (those clients deletable).
- Inbox, Settings/Config, login/onboarding/invite unchanged and green.
- app-shell NAV reduced to the residual set in residual mode; deep links resolve the correct Linear project per workspace.

Key files: apps/console/src/app/page.tsx, apps/console/src/components/workspace-home.tsx, apps/console/src/app/r/[owner]/[repo]/page.tsx, apps/console/src/components/app-shell.tsx

#### [M] Strangler regression gate: headless egress + Inbox + CLI boundary survive teardown
*depends on:* Delete orphaned dashboard/analytics/repo_home backend render routes (retain dashboard_priorities.py), Collapse root dashboard + repo pages to the residual status surface

Final safety task: explicit e2e proving nothing in the EXCLUDED set regressed. Prove: (1) ticket->tracker_poller->dispatcher->run.mjs still completes (agent_runs.py + runs.py untouched); (2) GitHub install + Linear webhook ingress still land (github_app.py / linear_webhook.py / *_oauth.py untouched); (3) the Inbox store reads/writes (inbox.py + tables untouched) AND the Inbox approval surface is reachable under residual + off console modes; (4) config.py GET/PUT round-trips the new scopes; (5) the Telegram bot's Navigator /chat/stream proxy still works; (6) dashboard_priorities.py + its model retained.

Acceptance criteria:
- Dispatch e2e (ticket -> run -> PR) green post-teardown.
- Install + webhook + OAuth callback e2e green.
- Inbox CRUD e2e green; Inbox approval reachable in residual + off; no inbox table dropped in any migration from this phase.
- config scope GET/PUT e2e green; Telegram /chat/stream proxy smoke green.
- OpenAPI diff shows ONLY console-render routes removed, zero EXCLUDED routes removed (dashboard_priorities.py present).

Key files: apps/backend/app/api/v1/routes/agent_runs.py, apps/backend/app/api/v1/routes/runs.py, apps/backend/app/api/v1/routes/inbox.py, apps/backend/app/api/v1/routes/github_app.py

### Phase 5 — Superstructure: adapters, self-spawn, autonomy dial

#### [M] Add 'ship' self-spawn provider adapter + wire into runAgent behind a flag
Create packages/cli/lib/agents/ship.mjs mirroring codex.mjs's ~30-LOC shape (spawn a CLI in workdir, return {agentId, branchName, status, exitCode}). Instead of 'codex exec' it invokes shipctl run nested on the same checkout — dogfood/debug the spawn+control loop in isolation (thesis 6). It bottoms out spawning the workspace's real provider (claude/cursor/codex) — does NOT duplicate the coding agent. Same guard pattern as codex.mjs (validate branchName + prompt; require env shipctl run needs, e.g. SHIP_API_TOKEN). Keep onLog + stdio convention identical.

Register runShipAgent in agents/index.mjs RUNTIMES (today cursor/codex/claude). Gate it: runAgent rejects provider==='ship' unless SHIP_ALLOW_SELF_SPAWN=true or opts.allowSelfSpawn, so a misconfigured ws can never silently fork-bomb. Add 'ship' to agent_provider_resolver AgentProviderKind + the allowed set, AND a NEW forward migration (next free after 0086, i.e. 0087) extending 0063's CHECK constraint ck_workspaces_agent_provider_enum from (cursor,codex,claude) to include 'ship' (do NOT edit 0063 — already applied; downgrade restores the 3-value set). Update the index.mjs + run.mjs docblocks.

FOUNDER DEFAULT: the ship provider is internal/dogfood-gated and NOT selectable by default customer workspaces.

Acceptance criteria:
- ship.mjs exports runShipAgent({workdir,branchName,prompt,env,onLog}) with the exact return shape as codex.mjs; spawns shipctl run (verified by argv in a unit test); throws on missing branchName/prompt; <= ~40 LOC ex-docblock.
- runAgent('ship', ...) throws unless SHIP_ALLOW_SELF_SPAWN/allowSelfSpawn set.
- New migration 0087 replaces the CHECK to allow 'ship', downgrades cleanly to the 3-value set.
- Existing cursor/codex/claude resolution unaffected (regression green).

Key files: packages/cli/lib/agents/ship.mjs, packages/cli/lib/agents/index.mjs, apps/backend/app/services/agent_provider_resolver.py, apps/backend/migrations/versions/0087_workspace_agent_provider_ship.py, packages/cli/lib/commands/run.mjs

#### [M] Force self-spawn recursion THROUGH cascade-depth + per-ws cap (recursion guard)
*depends on:* Add 'ship' self-spawn provider adapter + wire into runAgent behind a flag

Per thesis 6, recursion MUST pass through existing controls, not around them. maybe_dispatch (dispatcher.py) applies the cascade guard via _count_recent_dispatches against CASCADE_LIMIT=3 (dispatcher.py:148/:855) and the per-ws cap via active>cap (dispatcher.py:917). A nested shipctl run that triggers a dispatch must be counted the SAME as any other. Add an explicit trigger_kind 'self_spawn' that is NOT in the is_cascade carve-out (dispatcher.py:1072 — is_cascade bypasses the project lock; self-spawn must NOT get that bypass) and NOT in any cap exemption.

FOUNDER DECISION: the control plane is strictly off-limits to the autonomy dial — self_spawn gets no cap/cascade exemption under ANY profile, even ElMundi/high.

Acceptance criteria:
- trigger_kind='self_spawn' increments the same _count_recent_dispatches counter and is refused with CASCADE_BLOCKED at recent>=CASCADE_LIMIT.
- self_spawn dispatches count against the per-ws cap (active>cap -> CAP_EXCEEDED), no exemption.
- self_spawn excluded from the is_cascade project-lock bypass at dispatcher.py:1072 (acquires/respects project: lock like a fresh entrant).
- Test proves a 4-deep self-spawn loop terminates via CASCADE_BLOCKED, not infinitely; the same test passes regardless of autonomy profile.

Key files: apps/backend/app/services/dispatcher.py, apps/backend/app/db/models/agent_dispatch.py, apps/backend/tests/test_services_dispatcher.py

#### [M] Map autonomy profile to the code_review->auto_merge FSM gate (CI-green non-negotiable)
*depends on:* Add autonomy:{high|balanced|conservative} column to Workspace + migration 0086 (balanced backfill, pulled forward)

Per thesis 7 the dial lives partly at the FSM gate. The Phase-4 server gate _validate_code_review_to_auto_merge (agent_runs.py, commit 776def6a) requires >=1 APPROVED GitHub review AND all checks green before allowing code_review->auto_merge; on failure forces outcome=blocked. Make the APPROVAL requirement autonomy-sensitive while keeping CI-green NON-NEGOTIABLE at every level: conservative = require human APPROVED review + CI green (today); balanced = require CI green + reviewer-bundle verdict, allow agent self-approval at FSM level (no human click) + CI green; high = require CI green only (skip the human-approval check, trust the reviewer bundle + auto-merger gate). Read the ws autonomy (resolve_autonomy_for_workspace) in finish_agent_run before invoking the gate; record the active profile in the transition.validation_failed / advance audit payload.

FOUNDER DECISION: CI-green stays non-negotiable in ALL profiles; the control plane is off-limits to the dial (this gate loosens approval action-rights only, never cap/cascade/lease). high is opt-in.

Acceptance criteria:
- conservative: code_review->auto_merge still blocked without an APPROVED review (unchanged from 776def6a).
- high: allowed with CI green even without a human APPROVED review.
- ALL profiles still block when any completed check_run is not in {success,skipped,neutral} — CI-green never loosens.
- The audit payload records which autonomy profile evaluated the gate.

Key files: apps/backend/app/api/v1/routes/agent_runs.py, apps/backend/tests/test_v1_agent_runs_finish.py

#### [M] Inject autonomy profile into agent-role prompts + auto-merger via policy preamble (high requires knowledge)
*depends on:* Add autonomy:{high|balanced|conservative} column to Workspace + migration 0086 (balanced backfill, pulled forward)

Per thesis 7 the dial also lives at the agent-role-prompt + auto-merger layer; per thesis 5 ALL value enters through the prompt/context, never tool-native config. Render a per-profile autonomy preamble through the SAME centralised path as policies (policies.py render_policies_preamble — already byte-identical across Navigator chat and shipctl run). Action-rights blocks: high = skip optional approvals, broader edits, self-pick work, create tickets/decompose without confirm, self-merge-eligible; balanced = confirm destructive/structural ops; conservative = confirm before merge, narrow edits, no ticket creation without confirm. auto-merger.md STALL/BOUNCE/MERGE thresholds read the profile. Inject via the prompt assembly path, NOT cursor/.cursorrules/claude hooks/codex config.

Coupling guard (LOAD-BEARING, founder decision 'high requires rich knowledge injection'): when autonomy='high', the renderer (or a startup/seed check) must verify the ws has a non-empty knowledge surface (>=1 enabled policy AND/OR populated .ship/knowledge / Lighthouse binding) and warn/downgrade-to-balanced-with-audit if absent — high autonomy only works WITH rich knowledge injection.

Acceptance criteria:
- The autonomy preamble renders through the same module as the policies preamble (chat + CI agents byte-identical text).
- Each profile produces a distinct action-rights block (diffs visible in the rendered string).
- auto-merger.md references the profile in its STALL/BOUNCE/MERGE protocol.
- autonomy='high' on a no-knowledge ws emits an audited warning (or downgrades to balanced), never silently runs high-without-knowledge.
- No agent-tool-native config files written/read for the dial.

Key files: apps/backend/app/services/policies.py, apps/backend/app/services/agent/topic.py, apps/backend/app/resources/agent_roles/auto-merger.md, packages/cli/lib/commands/run.mjs

#### [S] Document the adapter discipline contract (thesis 5) as enforced docs + lint
*depends on:* Add 'ship' self-spawn provider adapter + wire into runAgent behind a flag

Codify thesis 5 so future adapters (and ship.mjs) stay agent-agnostic: the ONLY coupling allowed is (a) the non-interactive invocation contract and (b) 'the agent commits to the branch Ship checked out'. All Ship value (agent-roles, policies, .ship/knowledge, Lighthouse, autonomy preamble) injects THROUGH the prompt/context. Add a CONTRACT docblock at the top of packages/cli/lib/agents/index.mjs and a short doc enumerating the rule + the rejected Variant A (tool-native config/hooks/rules) + the rejected inversion (Ship becoming an MCP tool the agents call). Add a CI grep/lint check that fails if any adapter writes/reads a tool-native config file (.cursorrules, per-tool CLAUDE.md, codex config, *.mcp.json) — scoped to file-write/read calls, NOT string literals, so it does not become noise.

Acceptance criteria:
- index.mjs docblock states the two-point coupling contract + the two rejected patterns.
- A doc file captures the contract with per-tool rationale.
- A CI check fails if any file under packages/cli/lib/agents/ writes/reads a tool-native config artifact.
- All four adapters (cursor/codex/claude/ship) pass the check.

Key files: packages/cli/lib/agents/index.mjs, documentation/internal/architecture/agent-adapter-contract.md, apps/backend/tests/test_agent_adapter_discipline.py

### Phase 6 — Local synchronous executor (trigger a)

#### [M] Render a ticket-less / finish-less prompt variant for the local executor
run.mjs's renderExitProtocol (run.mjs:1686) hard-codes the sidecar-finish + PR-authoring contract. For trigger (a) there is no ticket, no finish, no PR — the agent edits the scratch tree and stops. Factor the prompt renderer so the local executor gets system body + role body + policies preamble + lifecycle-hooks WITHOUT the exit-protocol/sidecar/PR/{{ISSUE}} blocks. Extract renderPrompt (run.mjs:1580) + renderExitProtocol so a `local` mode opts out. Keep renderLifecycleHooks (knowledge fetch/feedback). Add an explicit 'this is a local scratch run; do not push, do not open a PR, do not call any finish endpoint; if this turns into a big feature, tell the operator to escalate' instruction (E20 pattern: suggest, never block).

RISK: refactoring the shared renderPrompt risks regressing the load-bearing (b) path — guard with existing run.mjs prompt tests before/after.

Acceptance criteria:
- The local prompt contains NO sidecar/finish/PR instructions and NO {{ISSUE}} ticket block.
- The local prompt still injects system base + role body + policies preamble (thesis 5).
- renderPrompt remains backward-compatible for run.mjs's ticket path (existing tests pass).
- The local prompt explicitly instructs not to push/PR/finish and to recommend escalation for big-feature work.

Key files: packages/cli/lib/commands/run.mjs, packages/cli/lib/commands/local.mjs

#### [M] Gate the local executor behind a flag (default-off everywhere) + extend the E20 intent classifier (ESCALATE verdict, suggestion-only)
*depends on:* Add autonomy:{high|balanced|conservative} column to Workspace + migration 0086 (balanced backfill, pulled forward)

(a) must be flag + classifier gated. Two gates: (1) a per-workspace feature flag (FOUNDER DECISION: default-OFF everywhere, including ElMundi — high autonomy does NOT auto-enable the local executor) allowing the local executor to run at all; (2) a classifier detecting when a local turn is actually a big async feature belonging in (b). Extend the E20 intent classifier (services/agent/drafting_intent.py — DraftingIntentService.classify, Verdict ENTER/EXIT/NEUTRAL at line 38) with a new ESCALATE verdict + regex fast-paths + LLM fallback for 'multi-file feature / needs a PR / needs review' signals, mirroring _ENTER_PATTERNS (drafting_intent.py:65).

FOUNDER DECISION: the classifier stays a SUGGESTION engine — on a misclassified big-feature local turn it SUGGESTS escalation (E20 pattern), it NEVER hard-blocks.

Acceptance criteria:
- A per-workspace flag gates whether the local executor runs; default-OFF for ALL workspaces (including ElMundi), sourced from the autonomy/config surface.
- drafting_intent.py gains an ESCALATE verdict with regex + LLM-fallback coverage, unit-tested for >=3 big-feature phrasings (EN + RU, matching the bilingual pattern set).
- The classifier defaults to NEUTRAL/no-escalate on any failure path (safe-fallback contract preserved).
- A clearly big-feature local request produces an escalate SUGGESTION, never a silent local run and never a hard block.

Key files: apps/backend/app/services/agent/drafting_intent.py, packages/cli/lib/commands/local.mjs, apps/backend/app/core/config.py

#### [L] Add a distinct local synchronous executor command (no lease, scratch checkout, stop before push)
*depends on:* Render a ticket-less / finish-less prompt variant for the local executor, Gate the local executor behind a flag (default-off everywhere) + extend the E20 intent classifier (ESCALATE verdict, suggestion-only)

Net-new (a). run.mjs is hard-gated on a ticket: getNextTask + exit EXIT_NO_TASK when none (run.mjs:285-302), takes a dispatch lease, and --commit-and-pr OWNS push + gh pr create + /agent-runs/finish. De-ticketing run.mjs would entangle the local path with all that control-plane logic. Instead add a SEPARATE command (proposed `shipctl local`, new file commands/local.mjs) that: (1) takes NO lease, never calls the dispatcher; (2) prepares a SCRATCH checkout/worktree off current head (reuse the spirit of prepareGitBranch but a throwaway worktree, not a deterministic ticket branch); (3) renders the trimmed prompt (prior task); (4) spawns via the existing runAgent(provider) adapter; (5) STREAMS output and STOPS — no push, no gh pr create, no POST /agent-runs/finish. Must NOT import the push/PR/finish helpers at all.

Acceptance criteria:
- The command NEVER calls /tracker/next, /agent-runs/finish, pushBranch, or openPullRequest — verified by grep over the new command and by a run leaving origin untouched.
- It NEVER acquires a dispatch lease (no dispatcher call); a concurrent (b) dispatch for the same workspace is unaffected.
- Work happens on a scratch checkout/worktree; the operator's working tree and any ticket branches are not mutated.
- Agent stdout/stderr streams in real time; on completion prints the scratch diff and exits without mutating any tracker/PR/lock state.
- Reuses runAgent(provider) — no new agent-spawn code.

Key files: packages/cli/lib/commands/local.mjs, packages/cli/lib/commands/run.mjs, packages/cli/lib/agents/index.mjs

#### [M] Wire a->b escalation via create_issue (NOT lease handoff) from the local executor
*depends on:* Add a distinct local synchronous executor command (no lease, scratch checkout, stop before push), Gate the local executor behind a flag (default-off everywhere) + extend the E20 intent classifier (ESCALATE verdict, suggestion-only)

Thesis 3: the a->b escalation seam is create_issue, NOT a lease handoff. When the local executor (or its classifier) decides a scratch session is really a big async feature, it escalates by CREATING A TICKET — which flows through the normal tracker_poller->dispatcher (b) path — never by handing its (nonexistent) lease to a dispatcher. The primitive exists: Navigator's _tool_ticket_create (tools.py:2141) calls the tracker create_ticket adapter. Add a path in the local executor that, on ESCALATE, calls the same server-side create-ticket surface (via the Ship API the CLI authenticates against with SHIP_API_TOKEN) to open a ticket capturing the scratch context (operator ask + pointer to / patch from the scratch diff), then prints the ticket ref. Scratch work is NOT auto-pushed; the new ticket's dispatched agent re-derives the change under the controlled (b) lease.

FOUNDER DECISION: escalation is operator-confirmed (suggestion-driven), never auto-forced — even under high autonomy the local turn proceeds and escalation is offered, consistent with the E20 suggest-not-block pattern. Dedup against a just-created ticket for the same session.

Acceptance criteria:
- Escalation creates a tracker ticket through the existing create_ticket adapter path — no new dispatch/lease code on the local side.
- The escalation NEVER acquires or transfers a dispatch lease (grep confirms no dispatcher/lease call in the local command).
- The created ticket carries enough context (operator ask + scratch summary/diff reference) for the downstream (b) agent.
- After escalation the command prints the new ticket ref and exits; the scratch tree is not pushed.

Key files: packages/cli/lib/commands/local.mjs, apps/backend/app/services/agent/tools.py

### Phase 7 — Chat-edge / inbound control (thesis 4)

#### [M] Harden comment-author disambiguation: replace _AGENT_COMMENT_MARKER_RE regex with Linear author identity
tracker_poller.py:157 _AGENT_COMMENT_MARKER_RE = re.compile(r'\[Ship\s+SDLC:role-[\w-]+\]') decides agent-vs-human by scanning the comment BODY for a marker string. This is fragile: a human who quotes/pastes the marker (e.g. replying inline to the agent's question) is misclassified as the agent, breaking _operator_answered_clarification (tracker_poller.py:160); and any drift in the marker format silently mis-detects. Linear already returns the structured author — list_comments (tracker_adapter.py) populates CommentRef.author from user{displayName,email}, and the poller's comments(first:20) GraphQL block (tracker_poller.py:102) can request user{email,isMe} / botActor similarly. Switch the primary signal to author identity: a comment is 'agent' iff authored by the workspace's service account (the PAT identity / Linear bot actor) — fall back to the marker regex ONLY as a secondary heuristic for legacy rows lacking author. Centralize in one helper consumed by both _operator_answered_clarification and the new comment-inbound path (next task) so there is a single source of truth for agent-vs-human.

OPEN QUESTION (founder): the canonical Linear identity of 'the workspace agent' (PAT user vs OAuth app/bot actor) differs per how each workspace's integration is provisioned; resolve the right field (user.isMe vs botActor) before relying on it.

Acceptance criteria:
- A comment whose body contains the literal marker string but authored by a human is classified as HUMAN.
- A comment authored by the workspace service account is classified as AGENT even if the marker text is absent/changed.
- The poller GraphQL comment fetch (comments(first:20)) returns the author identity field used for the decision (no extra round-trip).
- _operator_answered_clarification and the new comment-inbound path both call the single shared classifier; a unit test covers paste-the-marker-as-human and missing-marker-as-agent.
- Legacy comments lacking author identity still classify via the marker regex fallback (no regression on historical clarification tickets).

Key files: apps/backend/app/services/tracker_poller.py, apps/backend/app/integrations/linear/tracker_adapter.py

#### [L] Surface fresh Linear operator comments into a Navigator round-trip (context-only, never a transition)
*depends on:* Harden comment-author disambiguation: replace _AGENT_COMMENT_MARKER_RE regex with Linear author identity

Today a human reply on a Linear ticket only matters in the narrow clarification path (tracker_poller.py:160 _operator_answered_clarification -> _clear_clarification_and_dispatch, which strips needs:clarification and re-fires the SAME fsm_stage). For all other tickets, an operator comment is invisible until an agent happens to pull it via the get_ticket_snapshot tool (tools.py:4908, include_comments). Add a poller path that detects a NEW non-agent comment on an active ticket (reuse the comments(first:20) block already fetched in _poll_installation, tracker_poller.py:97-122, and the newest-first author walk) and feeds it into the Navigator as conversational inbound — append it as a user-role turn on the ticket's Navigator thread (chat.py POST /chat/stream, classify_shift=False) so the agent can answer/incorporate it.

CRITICAL INVARIANT (thesis 2): this MUST NOT call transition() or write a tracker.event.received that changes FSM stage — the STATUS field stays the only transition signal (tracker_fsm.py:277-281). Model it exactly on the clarification path's restraint: comment -> context/notification, not control. Gate behind a per-workspace flag (off by default, consistent with inbox-only launch egress) and dedupe on comment id (persist last-seen comment id alongside the existing poller cursor, _load_cursor/_save_cursor tracker_poller.py:398-462) so the same comment isn't re-injected each poll tick.

Acceptance criteria:
- A new non-agent comment on a flagged-on workspace's active ticket triggers exactly one Navigator turn on that ticket's thread; the comment body is passed as the user message with classify_shift=False.
- No code path added here calls transition(), issueUpdate on state, or emits a stage-changing tracker event — verified by a test asserting the ticket's FSM stage/status is unchanged after comment ingestion.
- Comment ingestion is idempotent: re-running poll_once with no new comment produces zero additional Navigator turns (last-seen comment id persisted and honored).
- Behavior is OFF by default; enabling requires the per-workspace flag.
- Agent comments (per the author-disambiguation task) are excluded so the agent never replies to itself.

Key files: apps/backend/app/services/tracker_poller.py, apps/backend/app/integrations/linear/tracker_adapter.py, apps/backend/app/api/v1/routes/chat.py, apps/backend/app/services/agent/tools.py

#### [M] Replace process-local _CHOICE_CACHE with a durable pending-action store
bot.py:233-250 _CHOICE_CACHE is a module-level OrderedDict mapping (chat_id, message_id) -> option list, populated when an inline keyboard is attached (bot.py:523) and popped on click (bot.py:737). run_with_leader_lock (bot.py:880) re-elects a new leader on every crash/deploy/autoscale event, and the new process starts with an EMPTY cache — so every previously-attached keyboard is dead-on-click. Replace with a durable store.

FOUNDER-LEANING (cheaper) option: reuse inbox_items (db/models/inbox.py:248) with a new type='chat_action' (extend the type set), storing the option list + chat_id + message_id + thread_id in payload (JSONB). NOTE the Console-pollution risk: inbox_items feed the Console Inbox via ix_inbox_workspace_status / ix_inbox_workspace_category_status and the status='new' default — a chat_action row would appear in operator inbox views unless explicitly filtered. Mitigate by setting category='dismiss_silently' AND adding a 'type != chat_action' filter to the Console list queries (api/v1/routes/inbox.py). Prefer payload JSONB to avoid schema churn; if a new column/index is needed it is a forward migration (next free after 0087). DECISION POINT for founder (see open questions): inbox_items reuse vs a dedicated telegram_pending_action table (cleaner isolation, one more table) — recommend the dedicated table if Console-filter airtightness is doubtful. Replace _cache_choice_options/_pop_choice_options with async DB read/write keyed on (workspace_id, chat_id, message_id).

Acceptance criteria:
- A keyboard attached before a simulated leader-failover (new bot process / cleared in-memory state) resolves correctly on click — the option list is read from the durable store, not memory.
- chat_action rows do NOT appear in the default Console Inbox list views (query test asserting type='chat_action' excluded or category='dismiss_silently' filtered out of operator surfaces).
- Single-use: once a choice is resolved (or buttons stripped), the durable row is marked consumed so a stale click can't re-fire (coordinates with the idempotency-key task).
- Existing in-group choice round-trip (on_choice_click, bot.py:708) still echoes the chosen label and runs the next Navigator turn on the same thread.
- If a new migration is added it is a forward revision (after 0087) and does not edit any applied migration in place.

Key files: apps/backend/app/integrations/telegram/bot.py, apps/backend/app/db/models/inbox.py, apps/backend/app/api/v1/routes/inbox.py

#### [M] Signed nonce + single-use idempotency key in Telegram callback_data, validated server-side
*depends on:* Replace process-local _CHOICE_CACHE with a durable pending-action store

callback_data is currently the unsigned, forgeable 'c|<idx>' (bot.py:300, parsed bot.py:729). Anyone who can craft a callback (or a stale/duplicated update) can replay an index against whatever option list now occupies that (chat_id, message_id) slot, and there is no idempotency guard beyond a best-effort edit_message_reply_markup(None) that races. Mirror the existing JWT signed-nonce pattern in integrations/telegram/bind_state.py (build_bind_nonce/verify_bind_nonce, HMAC via _secret(settings)=settings.jwt_secret, short TTL, replay-resistant) to sign callback_data: pack a signed token carrying {chat_id, message_id, option_idx, single-use nonce, exp}, staying under Telegram's 64-byte cap (use a compact token / store the heavy payload in the durable store from the previous task and put only an opaque short id+sig in callback_data). On click (on_choice_click) verify the signature + TTL, then atomically mark the nonce consumed in the durable pending-action store (single-use idempotency) BEFORE running the Navigator turn — a second click with the same nonce is rejected as already-consumed, eliminating the double-fire race the current markup-strip only partially covers.

Acceptance criteria:
- callback_data carries a server-verifiable signature; a tampered/forged callback_data fails verification and is rejected with a user-facing 'expired/invalid' answer, no Navigator turn run.
- The same callback fired twice (double-click / Telegram re-delivery) runs the Navigator turn exactly once — the second is rejected as already-consumed via an atomic single-use mark in the durable store.
- Expired tokens (past TTL) are rejected; TTL is configurable and defaults to a short window comparable to bind_state.
- callback_data stays within Telegram's 64-byte limit (test asserts encoded length for a representative payload).
- Signing secret is sourced the same way as bind_state._secret(settings) (settings.jwt_secret); no new secret-management surface introduced.

Key files: apps/backend/app/integrations/telegram/bot.py, apps/backend/app/integrations/telegram/bind_state.py

#### [M] Keep actor-sensitive approvals authoritative in Inbox/Console — Telegram button is a deep-link, not a commit point
*depends on:* Signed nonce + single-use idempotency key in Telegram callback_data, validated server-side

Thesis 4 + founder decision: the control plane never commits through chat. Today on_choice_click (bot.py:708) treats a button as a commit point — it directly posts the chosen value as the next Navigator turn. That is fine for LOW-STAKES idempotent pre-enumerated Navigator choices (mode-a, lock-free chat), but ANY actor-sensitive approval (e.g. an Inbox 'approval' item, inbox.py:266 type='approval', or anything that would loosen the control plane) must NOT be committable from a chat button. Add a classification at keyboard-build time (_build_choice_markup, bot.py:253): a directive flagged as approval/control-stakes renders a deep-LINK button (url= into Console/Inbox at the specific item) instead of a callback button, so the click navigates the operator to the authoritative actor-sensitive surface where the real approval (with the correct actor identity, not the shared group PAT) is recorded.

Cross-check with the Phase-4 'don't orphan pending operator approvals' constraint: ensure the deep-link lands on the reachable Inbox approval surface (which Phase 4 keeps reachable in every console mode). Document in code the boundary: callback button = low-stakes Navigator choice; url button = control-plane/approval deep-link.

Acceptance criteria:
- A directive marked as an approval / control-stakes action renders as a Telegram URL button deep-linking to the corresponding Console/Inbox item, NOT a callback button — verified by a unit test on _build_choice_markup.
- Clicking an approval deep-link never runs a Navigator turn nor records an approval from the shared group PAT identity; the approval is recorded only in Inbox/Console under the acting user's identity.
- Low-stakes pre-enumerated Navigator choices continue to render as signed callback buttons and round-trip as before.
- The deep-link target is a reachable Inbox approval surface (no orphaned pending approvals) even when the Console surface is otherwise reduced.

Key files: apps/backend/app/integrations/telegram/bot.py, apps/backend/app/integrations/telegram/render.py, apps/backend/app/api/v1/routes/inbox.py

### Phase 8 — Deterministic workflow primitive (thesis 8)

#### [S] W8.7 — Migration: workflow_runs + workflow_step_runs tables (durable run/step state + idempotency keys)
Net-new forward migration (HEAD chain is 0085->0086 autonomy->0087 ship-provider; this is the next free revision, e.g. 0088) creating two tables. workflow_runs: id, workspace_id, spec_name, spec_version, inputs (jsonb), trigger_kind (chat|gate|cron), status (queued|running|completed|blocked|failed), created_at, finished_at, audit linkage. workflow_step_runs: id, workflow_run_id (fk), step_id, attempt (int), kind, agent_provider, status, output (jsonb, validated against the step output_schema), lock_key (the workflow:<run>:<step> key tying it to agent_dispatch_locks), run_id (correlates to a CI coding-leaf agent run), created_at, finished_at, with a UNIQUE (workflow_run_id, step_id, attempt) constraint that is the durable idempotency key W8.2 relies on. Add the SQLAlchemy models under apps/backend/app/db/models/workflow.py. Do NOT edit any applied migration in place; this is a forward revision.

Acceptance criteria:
- alembic upgrade head applies cleanly off the prior revision and downgrade reverses it.
- UNIQUE (workflow_run_id, step_id, attempt) exists and a duplicate insert raises IntegrityError (the idempotency guarantee for W8.2).
- Models expose status enums and jsonb inputs/output columns; output is nullable until the step completes.
- lock_key + run_id columns let a step row be correlated to an agent_dispatch_locks row and a downstream CI agent run.
- No existing migration file is modified; revision chains forward off the current head.

Key files: apps/backend/migrations/versions/0088_workflow_runs.py, apps/backend/app/db/models/workflow.py

#### [M] W8.1 — Workflow spec format + loader (.ship/workflows/*.yaml) with parallel/pipeline/loop/barrier + structured-output schema
Define the deterministic workflow definition language and a loader. Net-new: apps/backend/app/services/workflow/spec.py (Pydantic models) + packages/cli/lib/workflow/loadSpec.mjs (CLI-side parser). A workflow spec lives at .ship/workflows/<name>.yaml in the customer repo and declares: name, version, inputs (typed), and an ordered list of steps. Each step has a kind from {parallel, pipeline, loop, barrier, synthesize, judge, verify} and an agent block selecting a leaf executor: either coding (spawns a tool CLI via runAgent — T5) or reasoning (runs a Navigator-style turn via the in-process subagent loop — reuse _run_subagent_loop in services/agent/tools.py:5885, role-prompted). parallel fans out N child steps; barrier joins them; pipeline chains step outputs->inputs; loop repeats a step until an until predicate or max_iters; synthesize/judge/verify are reasoning steps with a fixed role prompt that consume prior step outputs and emit a structured-output object validated against an inline JSON Schema (output_schema).

The spec is DECLARATIVE/bounded (a DAG with a fan-out cap) — no unbounded recursion or event subscription; it INVOKES and COMPLETES. Mirror the existing _SUBAGENT_MAX_TOOL_CALLS=25 / _SUBAGENT_MAX_SECONDS=300 budget convention from tools.py. The spec MUST carry a top-level max_fanout (default 4) and max_depth (default 2) that the loader rejects-on-exceed BEFORE any dispatch, so a malformed spec can't request a fork-bomb. Validate that every leaf agent.kind resolves to a known provider (claude/codex/cursor/ship) or the reasoning loop.

Acceptance criteria:
- A .ship/workflows/*.yaml with all 7 step kinds round-trips through the loader into typed objects; an unknown step kind or unknown agent provider raises a validation error naming the offending step.
- Loader rejects a spec whose declared max_fanout > a hard ceiling (e.g. 8) or whose static step graph exceeds max_depth, BEFORE any execution, with a clear error.
- synthesize/judge/verify steps require an output_schema and the loader rejects them if missing; the schema itself is valid JSON Schema.
- Spec models reuse the budget-naming convention (per-step max_tool_calls / max_seconds) defaulting to 25/300.
- Unit tests cover: valid full spec, each invalid case above, and a pipeline whose step output keys feed the next step's inputs.

Key files: apps/backend/app/services/workflow/spec.py, packages/cli/lib/workflow/loadSpec.mjs, apps/backend/app/services/agent/tools.py

#### [L] W8.2 — WorkflowDispatchGate: route EVERY workflow-spawned agent through the control plane
*depends on:* W8.1 — Workflow spec format + loader (.ship/workflows/*.yaml) with parallel/pipeline/loop/barrier + structured-output schema, W8.7 — Migration: workflow_runs + workflow_step_runs tables (durable run/step state + idempotency keys)

CRITICAL safety task. Net-new: apps/backend/app/services/workflow/gate.py — a thin wrapper every workflow leaf (coding OR reasoning) calls before it spawns. It MUST reuse the dispatcher primitives, not reimplement them: acquire a lock via dispatcher.acquire_lock (dispatcher.py:235) under a workflow-scoped key namespace workflow:<workflow_run_id>:<step_id> (so workflow locks are countable + sweepable like ticket:* and <bundle>:* keys), check the per-workspace cap via dispatcher.count_active_locks AFTER acquire then walk-back-on-exceed exactly like maybe_dispatch (dispatcher.py:909/:917), and enforce cascade depth via dispatcher._count_recent_dispatches against CASCADE_LIMIT=3 keyed on the workflow_run (a fan-out of K leaves counts as K dispatches in the cascade window). Reuse the T6 self-spawn recursion guard: a workflow leaf that is itself the ship provider (W8.6) increments cascade depth, and the in-process reasoning leaf reuses the _subagent_active flag (tools.py:5758) so a reasoning leaf cannot itself call run_workflow/run_subagent. Idempotency: the gate writes/reads a durable workflow_step_runs row keyed (workflow_run_id, step_id, attempt) so a retried tick does not double-spawn. On refusal the gate returns the same DispatchResult/_Reason vocabulary (CAP_EXCEEDED/CASCADE_BLOCKED/LOCK_HELD) and the runtime records a skipped/blocked step rather than crashing. Add a lock-key namespace doc-comment next to the existing ticket:/project:/<bundle>: namespaces in dispatcher.py.

FOUNDER DECISION: control plane is strictly off-limits to the autonomy dial — gate.py contains NO profile branch; even a 'high' workspace hits the same cap/cascade.

Acceptance criteria:
- A workflow with max_fanout leaves acquires exactly that many workflow:* locks; a fan-out that would push count_active_locks past the per-ws cap walks back the over-cap acquire (lock released) and the step is marked cap_exceeded — verified against an actual agent_dispatch_locks row count.
- A workflow that recursively spawns workflows is refused once cascade depth reaches CASCADE_LIMIT=3 (reuses _count_recent_dispatches over audit_log, not a new counter).
- A reasoning leaf has run_subagent/run_workflow tools filtered out (_subagent_active honored); attempting to call them returns the existing 'subagent cannot spawn' error.
- Re-invoking the gate for the same (workflow_run_id, step_id, attempt) is idempotent — no second dispatch / no second lock; a NEW attempt is required to re-run a failed step.
- Every gate decision writes an AuditLog row (workflow.step_dispatched / workflow.step_blocked) carrying workflow_run_id, step_id, reason.
- No autonomy-profile branch exists in gate.py (asserted by a test/grep).

Key files: apps/backend/app/services/workflow/gate.py, apps/backend/app/services/dispatcher.py, apps/backend/app/services/workflow/runtime.py

#### [L] W8.3 — Workflow runtime executor: schedule the DAG, drive leaves through the gate, collect structured outputs
*depends on:* W8.2 — WorkflowDispatchGate: route EVERY workflow-spawned agent through the control plane

Net-new: apps/backend/app/services/workflow/runtime.py — the engine that takes a loaded spec (W8.1) + a workflow_run row and executes it to completion. It topologically schedules steps; for parallel/barrier it awaits the fan-out set; for pipeline it threads outputs; for loop it iterates until the predicate or max_iters; for synthesize/judge/verify it invokes a reasoning leaf and validates the result against output_schema. CRITICAL: the runtime NEVER calls runAgent or _run_subagent_loop directly — it ONLY spawns through workflow/gate.py (W8.2), the single chokepoint. A coding leaf, once the gate grants a lease, fires workflow_dispatch on ship-agent-run.yml exactly as the dispatcher does (reuse dispatcher.dispatch_workflow / the WORKFLOW_FILE constant) so the spawned coding agent runs run.mjs->runAgent in CI and reports back via the existing agent-runs/finish webhook; the runtime correlates that finish back to the step via the workflow:* lock run_id. A reasoning leaf runs IN-PROCESS via _run_subagent_loop (reuse, role-prompted from the spec). The runtime persists per-step status/output to workflow_step_runs and the overall run to workflow_runs; it is bounded (returns when the DAG drains or a budget trips) — distinct from the FSM which never completes. Provide a single async entrypoint run_workflow(session, workspace_id, spec_name, inputs, trigger_kind) that the three triggers call.

DESIGN (per open question, lean event-driven): drive coding-leaf completion off the existing finish webhook + a reconcile tick rather than holding an in-process await; keep all runtime state in workflow_step_runs (DB) for cross-replica correctness.

Acceptance criteria:
- run_workflow executes a 3-step pipeline (reasoning->coding->synthesize) end-to-end in a test, threading step outputs and validating the synthesize output against its schema.
- A parallel+barrier workflow awaits all branches before the barrier; a branch failure is recorded without aborting siblings, and the barrier sees the partial set.
- A loop step terminates at max_iters even if the until-predicate never goes true (no infinite loop).
- grep/AST check confirms runtime.py contains NO direct call to runAgent, dispatch_workflow, or _run_subagent_loop except via gate.py (single chokepoint).
- A coding leaf's downstream agent-runs/finish webhook releases the corresponding workflow:* lock (correlated by run_id), mirroring how ticket dispatch releases on finish.
- workflow_runs + workflow_step_runs rows reflect terminal status (completed/blocked/failed) and the run object is returned to the caller (bounded completion).

Key files: apps/backend/app/services/workflow/runtime.py, apps/backend/app/services/workflow/gate.py, apps/backend/app/services/dispatcher.py

#### [S] W8.6 — `ship` self-spawn provider as a workflow coding-leaf executor, passing through cascade + cap
*depends on:* W8.2 — WorkflowDispatchGate: route EVERY workflow-spawned agent through the control plane, Force self-spawn recursion THROUGH cascade-depth + per-ws cap (recursion guard)

Reuse the Phase-5 ship self-spawn provider (packages/cli/lib/agents/ship.mjs, already registered in agents/index.mjs RUNTIMES + gated by SHIP_ALLOW_SELF_SPAWN) as a workflow coding-leaf executor (dogfood/debug). No duplicate adapter — this task WIRES the existing provider into the workflow path and proves the recursion guard at the workflow boundary. On the server side, the WorkflowDispatchGate (W8.2) MUST treat a ship-provider leaf as a recursion edge: it increments cascade depth in dispatcher._count_recent_dispatches accounting so a Ship-spawning-Ship-spawning-Ship chain is refused at CASCADE_LIMIT=3, and it counts against the per-ws cap. This is the one place workflow execution can become genuinely recursive, so it is the highest-leverage point for the recursion guard. No tool-native config/hooks injected — value flows only through the prompt (T5).

FOUNDER DECISION: the ship provider stays internal/dogfood-gated (flag/allowlist), not selectable by default customer workspaces; the control plane is off-limits to the autonomy dial so this recursion edge is counted under EVERY profile.

Acceptance criteria:
- A workflow whose leaf provider is 'ship' resolves to the existing Phase-5 adapter via runAgent('ship', ...).
- A workflow whose leaf provider is 'ship' has its spawn counted as a cascade edge — a 4th nested ship-leaf in the cascade window is refused with CASCADE_BLOCKED.
- ship.mjs injects context only through the prompt/argv to shipctl run (no tool-native config/hooks/rules written) — re-verified at the workflow boundary.
- The ship provider is gated to internal/dogfood and not selectable by default customer workspaces.
- The 4-deep refusal test passes regardless of autonomy profile.

Key files: apps/backend/app/services/workflow/gate.py, packages/cli/lib/agents/ship.mjs, packages/cli/lib/agents/index.mjs

#### [M] W8.4 — Chat trigger (a): invoke a workflow from Navigator via a run_workflow tool, lock-free escalation
*depends on:* W8.3 — Workflow runtime executor: schedule the DAG, drive leaves through the gate, collect structured outputs

Wire trigger (a) — chat. Add a run_workflow Navigator tool in services/agent/tools.py alongside the existing run_subagent (tools.py:1790/5588). The Navigator chat loop itself stays LOCK-FREE (thesis 3a); the tool does NOT acquire dispatch locks in the chat process — instead it persists a workflow_runs row in state=queued and returns immediately, and the runtime (W8.3) is driven by the gated path (a reconcile tick or an in-process task that goes THROUGH gate.py). This preserves the a->b escalation rule: chat creates the work item, the control plane owns the spawn. The tool is admin-gated (reuse _require_workspace_role(ROLES_ADMIN) like _tool_decomposition_start, tools.py:5658), filtered out inside a subagent (recursion guard via _subagent_active), and takes (workflow_name, inputs). Add the tool spec + dispatch entry to the _TOOL_DISPATCH map and the tool-spec list. Surface the resulting workflow_run id back to the user.

FOUNDER/OPEN: who may fire workflows from chat likely varies by autonomy profile (control plane stays fixed, but invocation rights vary) — default to admin-gated + internal-dogfood at launch.

Acceptance criteria:
- Navigator can call run_workflow(workflow_name, inputs); the chat process acquires NO agent_dispatch_lock (assert lock table unchanged at tool-return time) — escalation, not in-loop dispatch.
- The tool is admin-gated and filtered out when _subagent_active is set.
- Calling run_workflow with an unknown workflow_name returns a structured error listing available .ship/workflows specs.
- The created workflow_run id is returned to the user and the eventual spawn goes through gate.py (verified: cap/cascade still apply to the chat-triggered run).
- Tool spec is registered in the dispatch map and appears in the Navigator tool list.

Key files: apps/backend/app/services/agent/tools.py, apps/backend/app/services/workflow/runtime.py

#### [M] W8.5 — Ticket/gate (b) + cron (c) triggers: fire a workflow from an FSM gate and a nightly cron
*depends on:* W8.3 — Workflow runtime executor: schedule the DAG, drive leaves through the gate, collect structured outputs

Wire triggers (b) and (c). (b) Gate: allow an FSM transition or PR gate to invoke a workflow as a gate action — e.g. on code_review entry, fire the multi-axis-review workflow (W8.8). Add a hook in the tracker_fsm/dispatcher path that, for a configured stage, calls workflow runtime.run_workflow(trigger_kind='gate') THROUGH gate.py rather than (or in addition to) the normal single-routine dispatch. This must respect the same lock — it counts against the ws cap and cascade. (c) Cron: register a new nightly workflow cron in services/cron_jobs.py using the existing @cron_with_lock(lock=CronLockId.<NEW>)+register_cron pattern.

GROUNDING NOTE: CronLockId max in use is now 1024 (DEPLOYMENTS_RECONCILE) — reserve the next free integer (1025+), e.g. WORKFLOW_NIGHTLY=1025; do NOT reuse any retired id. The cron iterates workspaces (reuse _all_workspace_ids in cron_jobs.py) and for each enabled workspace enqueues the nightly review+techdebt workflow via run_workflow(trigger_kind='cron'). Cron-triggered workflows obey a WORKSPACE_BUNDLE-style separate accounting so they don't starve SDLC ticket dispatch (a workflow:* prefix cap distinct from the SDLC cap, mirroring WORKSPACE_BUNDLE_CAP=1 in dispatcher.py:1314).

FOUNDER DEFAULT: disabled / egress-off workspaces are skipped fail-closed.

Acceptance criteria:
- A configured FSM stage entry fires run_workflow(trigger_kind='gate') through gate.py; the workflow's spawns count against the workspace cap + cascade (verified).
- A new CronLockId (1025+, not a retired id) and a @cron_with_lock cron registered via register_cron with a nightly cron_expr; the cron skips cleanly when another replica holds the lock (reuses single-leader election).
- The nightly cron iterates workspaces via _all_workspace_ids and enqueues per enabled workspace; disabled/egress-off workspaces are skipped fail-closed.
- Cron/gate-triggered workflow dispatches use a separate accounting prefix so they cannot exhaust the SDLC ticket cap (mirrors WORKSPACE_BUNDLE_CAP).
- Shadow mode (tracker_poll_fire off) records 'would have run' workflow audit rows without spawning, mirroring maybe_dispatch shadow behavior.

Key files: apps/backend/app/services/cron_jobs.py, apps/backend/app/services/cron.py, apps/backend/app/services/dispatcher.py, apps/backend/app/services/tracker_fsm.py

#### [M] W8.8 — First dogfood workflows: multi-axis PR review+verify and nightly codebase audit, + /process boundary doc
*depends on:* W8.5 — Ticket/gate (b) + cron (c) triggers: fire a workflow from an FSM gate and a nightly cron, W8.4 — Chat trigger (a): invoke a workflow from Navigator via a run_workflow tool, lock-free escalation

Author the first two internal/dogfood workflow specs and prove the relationship-to-/process boundary. (1) .ship/workflows/pr-review.yaml — a parallel fan-out of N reasoning leaves each reviewing one axis (correctness / security / simplification / test-coverage), a barrier, then a synthesize step (structured output: list of findings with severity) and a verify step that re-checks the highest-severity finding against the diff. Fired from the code_review FSM gate (W8.5 trigger b). (2) .ship/workflows/codebase-audit.yaml — a pipeline: a coding leaf (ship or claude provider) enumerates hotspots -> parallel reasoning leaves audit each -> judge step ranks tech-debt items -> emits a structured report. Fired nightly via cron (W8.5 trigger c).

CRITICAL boundary doc (thesis 8): add a README/section under .ship/workflows/ stating these are imperative bounded pipelines that COMPLEMENT, never duplicate, the /process editor (processes.py) — /process defines the reactive per-ticket SDLC state machine (ProcessNodeType states + schedule/event/manual triggers), the workflow primitive is a deterministic bounded fan-out you INVOKE for one job. Do NOT add workflow execution into processes.py and do NOT add state-machine semantics into the workflow runtime.

Acceptance criteria:
- pr-review.yaml loads via W8.1, runs via W8.3, and produces a schema-valid findings object from its synthesize step; the verify step consumes the top finding.
- codebase-audit.yaml runs as a pipeline (coding->parallel reasoning->judge) and emits a ranked tech-debt report; the nightly cron (W8.5) enqueues it per workspace.
- Both workflows' fan-outs are gated (cap/cascade) — a manually inflated max_fanout is rejected by the loader/gate, not at runtime.
- README explicitly states workflow = imperative bounded pipeline vs /process = reactive SDLC state-machine, with the rule 'do not merge or duplicate'; processes.py is unmodified by this workstream (verified by diff).
- End-to-end dogfood: triggering pr-review on a real Ship PR produces a synthesized multi-axis review without exceeding the workspace dispatch cap.

Key files: .ship/workflows/pr-review.yaml, .ship/workflows/codebase-audit.yaml, .ship/workflows/README.md
