---
rfc: 0010
title: "Plays, Automations, Runs, Inbox — operator IA"
status: Draft
created: 2026-04-23
supersedes_in_part: [rfc-0008]
follows: 0008
---

# RFC-0010 — Plays, Automations, Runs, Inbox

## Abstract

The console today exposes the orchestrator's internal ontology
directly to operators: `Pattern`, `Lane`, `Request`, `Fleet lane`,
`Fleet request`, `Pipeline`. Six entity terms compete for one mental
slot ("how do I make Ship do something?"), navigation duplicates the
same surfaces twice (workspace mode vs repo mode; per-repo and
fleet variants of lanes/requests), and human-in-the-loop signals
(clarifications, improvements, feedback, failed runs) live on four
separate routes with no single attention surface.

This RFC reframes the operator-facing console around **four
surfaces** — `Plays`, `Automations`, `Runs`, `Inbox` — and hides
the internal `Pattern` / `Lane` / `Workflow` vocabulary inside the
backend. Specifically:

1. **Catalog reframe.** `Pattern` becomes an internal executable
   unit. `Play` is the user-facing object: a ready-made operational
   procedure (e.g., *Technical audit*, *PR review*, *Release
   readiness*) that compiles down to one or more patterns with a
   default execution mode.
2. **Assignment-first UX.** Operators don't build automations — they
   pick a Play and assign it (this repo / selected / fleet, with a
   cadence). Fleet collapses into a scope parameter; `/fleet/*`
   routes retire.
3. **Outcome-first Runs.** The Pipelines list becomes Runs and
   shows what each execution *produced*, not just that it dispatched.
4. **Single attention surface.** A new top-level `Inbox` surface
   absorbs clarifications, improvements, failure escalations,
   approvals, and policy exceptions. Single owner per item, typed
   dispositions, audit trail.
5. **Routing as a first-class workspace concern.** Plays declare
   abstract escalation handles (`code_owner`, `pr_author`,
   `release_manager`); workspaces map handles to users / groups /
   strategies via routing rules in DB.

`Lane` stays as the persisted record in `.ship/config.yml`
(internal). `Pattern` stays as the catalog primitive. `Workflow`
stays as the orchestration mode of a composite Play. None of these
appear in operator-facing copy.

## Implementation status

| Phase | Scope | Status |
|---|---|---|
| 1 | IA rename + collapse (routes, sidebar, fleet retirement) | planned |
| 2 | Inbox v1 + routing model + member groups | planned |
| 3 | Outcome-first Runs (RunSummary contract, escalation linkage) | planned |
| 4 | Plays polish (categories, Coverage tab, Automate banner) | planned |
| 5 | External channels (email / Slack / Teams) | out of scope |

Tactical execution plan (tickets, risks, DoD per phase) lives in
[`documentation/internal/inbox-redesign-planning.md`](../internal/inbox-redesign-planning.md).

## Motivation

Three observations:

1. **The console exposes orchestrator internals, not product
   objects.** A bizdev operator opening the sidebar today sees
   *Lanes / Requests / Fleet lanes / Fleet requests / Pipelines*.
   None of these are how the customer *thinks* about their work —
   they think "run a technical audit on these repos every Monday",
   not "create a lane that schedules a parallel pattern fan-out".
   The current IA is a low-code orchestrator UI, not a product UX.
2. **Fleet vs per-repo is a false split.** `/lanes` and
   `/fleet/lanes`, `/requests` and `/fleet/requests` are the same
   object viewed at different scopes. Promoting scope to a section
   instead of a parameter doubles the navigation surface, doubles
   the implementation, and forces operators to learn two parallel
   hierarchies for a single concept.
3. **Human-in-the-loop is fragmented.** Clarifications,
   improvements, artifact feedback, and failed runs all signal
   "something wants your attention" but live on four routes with
   no unified queue, no ownership model, no SLA, and no
   cross-routing. Items rot. Nobody owns them, so nobody answers
   them. The operator's *attention* has no home.

## Terms

- **Play** — a user-facing operational procedure in the catalog.
  Atomic (one underlying pattern) or composite (multiple patterns
  with an execution mode). Has a category, a default execution
  policy, and an `inbox.profile`.
- **Automation** — an assignment of a Play to a scope (repo /
  selected / fleet) with a cadence (cron / event). Compiles to one
  or more `lane` rows in the affected repos' `.ship/config.yml`.
- **Run** — a single execution of a Play, manual or scheduled.
  Carries a structured `RunSummary` describing outcome.
- **Inbox item** — a work item requiring human disposition. Has
  exactly one owner at any moment. Five types: `clarification`,
  `improvement`, `failure`, `approval`, `exception`.
- **Handle** — a symbolic role declared by a Play (e.g.,
  `code_owner`, `release_manager`). Resolved to a concrete user by
  workspace routing rules.
- **Group** — a workspace-level operational set of users (e.g.,
  `secops`, `on-call`). Distinct from permission roles.
- **Disposition** — a typed action that resolves an Inbox item.
  Type-specific (e.g., Approval items have `Approve` /
  `Reject` / `Request changes`; Improvement items have
  `Accept` → creates Automation / `Decline` / `Defer`).
- **Coverage** — the count of activated repos that have a given
  Play assigned, expressed as `N/M repos`.
- **Pattern** — *(internal)* the atomic executable artifact. Lives
  under `artifacts/patterns/<id>/ARTIFACT.md`. Not user-facing.
- **Lane** — *(internal)* the persisted record in
  `.ship/config.yml` representing an Automation. Not user-facing.
- **Workflow** — *(internal)* the orchestration mode of a composite
  Play (`parallel` / `sequential` / `separate_workflows`). Not
  user-facing.

## Product model

### Surfaces

The console exposes exactly four operator-facing surfaces:

| Surface | Kind | Purpose |
|---|---|---|
| **Plays** | content | Catalog of procedures organised by category. `Run now` and `Automate` per card. |
| **Automations** | content | Assignments timeline. Coverage tab. Edit / pause / delete. |
| **Runs** | content | Outcome-first execution history with deeplinks to escalations. |
| **Inbox** | **attention** | Single home for everything that requires human disposition. |

**Architectural axiom.** Plays / Automations / Runs are read-mostly
content surfaces. Inbox is the only action surface. New features
are filed under "is this for understanding or for action?" — the
answer determines the surface. Action surfaces never hide inside
content surfaces.

### Navigation

Workspace-level sidebar (locked):

```
Home · Inbox · Plays · Automations · Runs · Knowledge · Settings
```

Repo context is a drill-down filter, not a parallel hierarchy:

```
← Workspace · Repo Home · Repo Settings
```

All other content surfaces (Inbox / Plays / Automations / Runs)
accept `?repo=<id>` and render a filtered view, with a breadcrumb
back to the workspace surface.

`Settings` carries the existing sub-pages plus two new ones:

```
Settings
├── Members              ← tabs: Members | Groups
├── Inbox routing        ← rules table + handle resolver preview
├── Policy
├── Integrations
├── Tracker / FSM
├── Catalog feedback     ← admin-only; relocated from /artifact-feedback
└── Audit log
```

### Repo Home composition

Order = action → recent → steady → discovery:

1. **Waiting on you (this repo)** — Inbox-filtered, top 5 with "see all"
2. **Recent runs (last 10)** — outcome strip
3. **Active automations** — list with next-run-at
4. **Coverage hint** — "3 plays cover this repo · 8 suggested"

## Catalog model — Plays

### Categories

Seven categories cover the current 77 user-facing patterns. Final
mapping in
[`documentation/internal/inbox-redesign-planning.md`](../internal/inbox-redesign-planning.md)
§3.

| Category | Approx. count |
|---|---:|
| Code review | 12 |
| Health checks (sub-facets: Security · Performance · Compliance · Cost · ML quality) | 32 |
| Release ops | 10 |
| Incident response | 5 |
| Knowledge & Docs | 4 |
| Planning & Process | 6 |
| Reviewers (role personas, standalone) | 8 |

### Atomic vs composite

A Play maps to either one Pattern (atomic) or several Patterns with
an execution mode (composite). Composites are **Ship-curated** —
operators cannot define new composites in v1.

Composite execution modes:

- `parallel` — patterns run in parallel branches
- `sequential` — patterns run one after another, short-circuiting on failure
- `separate_workflows` — each pattern runs as its own GitHub Actions workflow

Each composite Play has one default mode; operators do not choose
the mode in v1. Advanced override is deferred.

### Catalog frontmatter additions

Each pattern's `ARTIFACT.md` adds two fields under `spec`:

```yaml
spec:
  install_target: prompts/scan/security-deps.md
  category: scan
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: scan_with_autofix          # one of nine profiles, see below
    overrides:                           # optional per-Play overlay
      improvement:
        when: [recurring_cve_detected]
  critical: false                        # optional; flags Play for coverage red badges
```

Composite Plays (catalog-curated) carry an extra `composite` block:

```yaml
spec:
  composite:
    includes:
      - role-tech-architect
      - role-qa-architect
      - role-security-officer
    execution_mode: parallel
```

Plays without `composite` are atomic.

## Inbox model

### Item lifecycle

Four states:

| State | Meaning |
|---|---|
| `new` | Created, awaiting first action |
| `snoozed` | Hidden until `snoozed_until` |
| `resolved` | Disposition completed positively (terminal) |
| `dismissed` | Closed without action — won't-do / dup (terminal) |

### Types and dispositions

| Type | Allowed dispositions | Resolution side-effect |
|---|---|---|
| `clarification` | Answer · Reassign · Snooze · Dismiss | UPDATE `clarifications.answer` and `status` |
| `improvement` | Accept · Decline · Defer · Reassign | UPDATE `improvements.decision`; `Accept` enqueues Automation creation |
| `failure` | Retry · Disable automation · Acknowledge · Reassign | optional pipeline retry; optional `lane.enabled = false` |
| `approval` | Approve · Reject · Request changes · Reassign | callback to originating run; payload returned to agent for resume |
| `exception` | Allow once · Update policy · Pause automation · Reassign | optional workspace_policy override |

### Run → Inbox threshold

Run records always land in `pipeline_runs`. Inbox items are
created **only** on dysfunction or required disposition:

| Situation | Run | Inbox item |
|---|:---:|:---:|
| Run succeeded with findings → all in `RunSummary` | yes | no |
| Run produced an `outcome.requires_approval` payload | yes | yes (`approval`) |
| Manual one-shot run (`Run now`) failed | yes | **always** yes (`failure`, owner = `requested_by`) |
| Scheduled / event-driven run failed once | yes | no |
| Scheduled / event-driven run failed 3× consecutively | yes | yes (`failure`) |
| Pattern emitted `clarification` via tool call | yes | yes (`clarification`) |
| Pattern emitted `improvement` proposal | yes | yes (`improvement`) |
| Policy / budget exception requested | yes | yes (`exception`) |

The single rule that protects Inbox from noise: **a run that
needs nothing from a human does not generate an Inbox item.**

### Single-owner discipline

Every Inbox item has exactly one owner at any moment. Group-typed
routing resolves to one user at intake (round-robin / on-call /
first). Reassignment is a first-class disposition; group views
exist (`Mine` / `My team` / `Unassigned`) but the assignment field
itself is single-valued. Multi-owner / shared queues are explicitly
out of scope for v1.

### External channels

Out of scope for v1. Email, Slack, Teams, PR comment ingestion are
each separate adapter products with auth, threading, dedup, and
reply semantics. v1 Inbox carries Ship-internal items only.

## Routing model

Routing is layered into three concerns:

### A. Plays declare abstract handles

In `ARTIFACT.md` frontmatter:

```yaml
inbox:
  profile: flow_pr
  overrides:
    clarification:
      when: [missing_test_evidence]
```

A profile bundles default `(handle, when[])` per inbox type. Nine
profiles cover the catalog (full definitions in planning doc §3.1):

`silent` · `scan_default` · `scan_with_autofix` · `flow_pr` ·
`flow_release` · `flow_incident` · `flow_reporting` ·
`role_reviewer` · `onboarding`.

Pattern-level `overrides` overlay specific fields without
restating the whole profile.

### B. Workspaces map handles to targets

`inbox_routing_rules` table. Each rule maps one handle key to one
of three target types:

- `user` — direct user assignment
- `group` — operational group + assignment strategy
- `strategy` — runtime resolver (`pr_author`, `codeowners`,
  `requested_by`, `repo_role`, `repo_metadata`)

```yaml
handles:
  workspace_owner:    { target: group:workspace-owners,    assignment: round_robin }
  security_officer:   { target: group:secops,              assignment: round_robin }
  ops_oncall:         { target: group:ops-oncall,          assignment: oncall }
  pr_author:          { strategy: pr_author }
  code_owner:         { strategy: codeowners }
  requested_by:       { strategy: requested_by }
  repo_maintainer:    { strategy: repo_role,     role: maintainer }
```

Storage is DB-primary. A YAML export endpoint exists (for
compliance / version-controlled config snapshots); YAML *import*
is out of scope for v1.

### C. Mandatory fallback

Every workspace has a non-removable fallback rule (typically
`group:workspace-owners`, `round_robin`). Items whose primary
routing returns no owner land here, and the `intake_reason` field
records the fallthrough reason for diagnostic UI ("couldn't resolve
`code_owner`, fell back to workspace owners — fix?").

### Resolver order at intake

1. Read pattern `inbox.profile` + apply `overrides` → yields the
   `(handle, when[])` for the emitted item type
2. Look up handle in `inbox_routing_rules` for the workspace
3. If `target_type=strategy`, invoke resolver with run context
   (PR ref, `requested_by`, repo metadata)
4. If resolver yields a group → apply `assignment_strategy`
   (`round_robin` consults `group_assignment_state` transactionally)
5. If any step yields nothing → fall through to the mandatory
   fallback
6. Persist `inbox_item.owner_user_id`, `intake_handle`,
   `intake_reason`; write `assigned` event to
   `inbox_item_events`

### Operational groups vs permission roles

Permission roles (`owner` / `admin` / `maintainer` / `member` /
`viewer`) gate *what a user can do*. Operational groups
(`secops` / `on-call` / `release-managers` / …) describe *what a
user is responsible for*. They are orthogonal axes — one user can
be `member` (permission) and `secops` (group). Both live under
`Settings → Members` as sibling tabs.

## RunSummary contract

`pipeline_runs.outcome` JSONB carries a structured shape that
both the Runs list and the Inbox intake pipeline depend on:

```typescript
type RunSummary = {
  // Single-line UI sentence; pattern-authored, no UI derivation.
  outcome_text?: string;     // e.g. "3 issues found · 1 PR opened"
  headline?: string;

  findings_count?: number;
  findings_by_severity?: {
    low?: number;
    medium?: number;
    high?: number;
    critical?: number;
  };

  artifacts?: Array<{
    type: string;            // 'pr' | 'issue' | 'comment' | 'doc' | …
    title: string;
    ref?: string;            // url or external id
  }>;

  // Top-level — drives `approval` Inbox intake.
  requires_approval?: boolean;
  approval_payload?: Record<string, unknown>;

  // Already-emitted escalation hints, for diagnostic UI.
  escalations?: Array<{
    type: 'clarification' | 'improvement' | 'failure' | 'approval' | 'exception';
    reason: string;          // matches `when:` keys from profile
  }>;
};
```

`outcome_text` is **authored by the pattern**, not derived in the
UI. This avoids every consumer rolling its own copy logic.
`requires_approval` is top-level (not buried in `escalations[]`)
because it's the most consequential field — it directly creates an
Inbox item.

## Data model

Five new tables and one extension to `pipeline_runs`. Full DDL
lives in the planning doc §4. Summary:

- `member_groups`, `member_group_members` — operational groups
- `group_assignment_state` — round-robin pointer per group
- `inbox_routing_rules` — handle → target mapping
- `inbox_items` — the inbox itself, with `(source_table,
  source_id)` weak-FK to legacy `clarifications` /
  `improvements`
- `inbox_item_events` — audit trail
- `run_escalations` — many-to-many between `pipeline_runs` and
  `inbox_items`

The migration backfills `inbox_items` from existing
`clarifications` and `improvements` rows; the legacy tables stay
as write-side stores (for backwards-compat with agent tools and
tracker projection).

## Migration & backwards-compat

### URL redirects (Phase 1)

| Old | New |
|---|---|
| `/lanes` | `/automations` |
| `/lanes?tab=library` | `/plays` |
| `/lanes/[laneRowId]` | `/automations/[id]` |
| `/requests` | `/plays` (with `?mode=request` filter) |
| `/pipelines` | `/runs` |
| `/pipelines/[pid]/runs/[rid]` | `/runs/[rid]` |
| `/clarifications` | `/inbox?type=clarification` |
| `/improvements` | `/inbox?type=improvement` |
| `/artifact-feedback` | `/settings/catalog-feedback` |
| `/fleet/lanes` | `/automations?scope=fleet` |
| `/fleet/lanes/new` | `/automations/new?defaultScope=fleet` |
| `/fleet/requests` | `/runs?trigger=manual&scope=fleet` |
| `/fleet/requests/new` | `/plays` (with `Run now` → fleet wizard) |
| `/fleet/requests/[id]` | `/runs/[id]` |
| `/fleet/policy` | `/settings/policy` |
| `/fleet/adoption` | `/automations?tab=coverage` |
| `/fleet/knowledge` | `/knowledge?scope=fleet` |
| `/r/.../lanes` | `/automations?repo=<id>` |
| `/r/.../requests` | `/plays?repo=<id>` |
| `/r/.../clarifications` | `/inbox?type=clarification&repo=<id>` |
| `/r/.../improvements` | `/inbox?type=improvement&repo=<id>` |

All redirects are 301 with a `?from=legacy_<route>` query param
for telemetry. Held for 30 days post-launch, then removed.

### Internal vocabulary preserved

`lane`, `pattern`, `workflow` are **not renamed** in the backend,
in `.ship/config.yml`, in API endpoints, or in the Python codebase.
This RFC is a UI / IA reframe, not a backend ontology rewrite.

### `.ship/config.yml` schema

Unchanged. Automations compile down to lane rows in the same
schema RFC-0007 defined. The catalog gains an `inbox.profile`
field on patterns (Phase 2 catalog PR), but no existing field is
renamed or removed.

## Relationship to prior RFCs

- **RFC-0007 (Lanes-as-config and `shipctl run`)** — preserved
  verbatim. `Lane` remains the persisted record; `shipctl run
  --lane <id>` remains the dispatch entry point. This RFC sits
  *above* RFC-0007 in the stack, addressing UX/IA concerns
  without touching the lanes pipeline.
- **RFC-0008 (Catalog reform)** — partially superseded for
  *user-facing terminology and IA only*. The pattern frontmatter
  (`category` / `modes` / `default_trigger` / `inputs`), the
  `<category>-<name>` rename, and the catalog-as-source-of-truth
  contract all stand. This RFC reframes how that catalog is
  *presented* to operators (Plays, not raw patterns) and how
  Lanes/Requests *navigate* in the UI (collapsed under Plays /
  Automations / Runs / Inbox). Pattern files gain an `inbox.profile`
  field; nothing is removed.
- **RFC-0009 (Catalog Phase-2 expansion)** — orthogonal. The 50
  new patterns RFC-0009 added all map to one of the nine inbox
  profiles defined here.

## Out of scope

This RFC explicitly does not cover:

- Email / Slack / Teams ingestion
- Inbox SLA matrix or escalation chains beyond a stale badge
  (2d yellow / 7d red) and manual reassign
- Workspace-defined composite Plays
- Multi-owner inbox items / shared queues
- Visual rule builder for routing
- Per-Play policy override editor surfaced to operators
- Inbox item nested threads or replies
- Inbox cross-workspace queries
- Mobile-optimised Inbox UI
- Coverage matrix / heatmap (planning doc §2.Q2 commits to a
  list-with-progress-bars view; matrix view deferred to v2)

## Phasing

Tactical breakdown — tickets, risks, definitions of done — lives
in [`documentation/internal/inbox-redesign-planning.md`](../internal/inbox-redesign-planning.md).
Headline phasing:

| Phase | Theme | Target |
|---|---|---|
| 1 | IA rename + collapse (routes, sidebar, fleet retirement) | 1.5 weeks |
| 2 | Inbox v1 + routing model + member groups | 3-4 weeks |
| 3 | Outcome-first Runs (`RunSummary` contract, escalation linkage) | 2 weeks |
| 4 | Plays polish (categories, Coverage tab, Automate banner) | 1 week |
| 5 | External channels | out of scope |

Each phase has its own DoD; the planning doc tracks ticket-level
progress per phase.
