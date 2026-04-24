# Plays / Automations / Runs / Inbox — execution planning

Tactical execution doc for [RFC-0010](../protocol/rfc-0010-plays-and-inbox.md).
Single source of truth for the redesign sprint: locked decisions,
ticket-level phase breakdown, risks, definition of done, day-1
actions.

The RFC carries the design contract; this doc carries the
execution detail. Treat anything in here as living — strike
through tickets as they ship, append follow-ups in each phase's
"Deferred" sub-section (mirroring the
[`console-refactor-backlog.md`](console-refactor-backlog.md)
convention).

---

## §1 · Locked decisions

Carried over from pre-planning conversation. Do not re-litigate.

### Naming

- Catalog item = **Play**
- Backend `lane` term unchanged
- Five inbox types: `clarification` · `improvement` · `failure` · `approval` · `exception`
- `ArtifactFeedback` exits operator inbox → admin-only at `/settings/catalog-feedback`

### Engineering policies

| Policy | Value |
|---|---|
| Routing storage | DB primary; YAML export available; no YAML import in v1 |
| Failure → Inbox threshold | scheduled/event = 3 consecutive · manual = always |
| Group owner picker default | `round_robin` (with `last_assigned_user_id` state) |
| Run summary contract | unified shape (RFC-0010 §RunSummary) |
| Composite Plays | Ship-curated catalog only; workspace cannot define new composites in v1 |
| External channels | OUT of v1 |

### Resolved IA questions

| Q | Resolution |
|---|---|
| Plays categories | 7 categories (see §3) |
| Coverage view | List with progress bars + critical-uncovered red badge; matrix deferred to v2 |
| Settings IA for Members/Groups/Routing | Members + Groups in one page (tabs); Inbox routing as separate Settings sub-page |
| Repo Home v1 | `Waiting on you · Recent runs · Active automations · Coverage hint` (in this order) |

---

## §2 · Plays categories — final mapping

### Seven categories

```
1. Code review              12 patterns
2. Health checks            32 patterns  (sub-facets: Security · Performance · Compliance · Cost · ML quality)
3. Release ops              10 patterns
4. Incident response         5 patterns
5. Knowledge & Docs          4 patterns
6. Planning & Process        6 patterns
7. Reviewers (role personas) 8 patterns
                            ──
                            77 user-facing Plays
```

Patterns excluded from the user-facing catalog (system-internal,
profile = `silent`):

- `common-base`, `common-kickoff`
- `op-retry-sweep`, `op-stale-issue-sweep`, `op-workflow-self-heal`

### Per-category content

**Code review** (12) — PR-attached flows.

`flow-pr-self-review` · `flow-blast-radius` · `flow-qa-acceptance` ·
`flow-preview-validation` · `flow-preview-failure-recovery` ·
`flow-check-failure-recovery` · `flow-human-handoff` ·
`scan-test-coverage` · `scan-api-contract` · `scan-tech-debt` ·
`scan-dead-code` · `scan-docs-freshness`

Composite candidates (catalog-curated):

- *PR review* = `flow-pr-self-review` + `flow-blast-radius` + `scan-test-coverage` (separate workflows)
- *Technical audit* = `role-tech-architect` + `role-qa-architect` + `role-security-officer` (parallel)

**Health checks** (32) — scheduled scanners.

Sub-facets (UI sub-grouping inside the category):

- *Security* — `scan-security-deps` · `scan-license-deps` · `scan-pii-leakage` · `scan-permissions-audit` · `scan-iam-policy-diff` · `scan-k8s-policy` · `scan-signing-notarization` · `scan-audit-log-integrity`
- *Performance* — `scan-performance-budget` · `scan-app-size-budget` · `scan-firmware-size` · `scan-installer-size` · `scan-asset-budget` · `scan-power-profile` · `scan-build-frametime` · `scan-mobile-crash-rate` · `scan-slo-health`
- *Compliance* — `scan-consent-drift` · `scan-store-metadata` · `scan-localization-gap` · `scan-os-support-matrix` · `scan-hal-abi-lock`
- *Cost* — `scan-cost-delta` · `scan-terraform-drift` · `scan-env-var-catalog`
- *ML quality* — `scan-data-drift` · `scan-bias-fairness` · `scan-model-eval` · `scan-feature-schema` · `scan-training-repro`
- *Other* — `scan-a11y` · `scan-bom-delta` · `scan-sbom-drift`

**Release ops** (10).

`flow-release-notes` · `flow-store-submission` · `flow-cert-compliance` ·
`flow-compliance-artifact` · `flow-autoupdate-rollout` · `flow-ota-channel` ·
`flow-beta-distribution` · `flow-model-card` · `flow-dependency-update` ·
`flow-live-ops-calendar`

Composite candidate: *Release readiness* = `flow-release-notes` +
`flow-compliance-artifact` + `flow-dependency-update` +
`scan-test-coverage` (sequential).

**Incident response** (5).

`flow-incident-postmortem` · `flow-oncall-handoff` ·
`flow-runbook-freshness` · `flow-human-handoff` · `role-clarification`

**Knowledge & Docs** (4).

`scan-docs-freshness` · `flow-runbook-freshness` ·
`flow-learning-capture` · `onboard-seed-knowledge`

> Note: `scan-docs-freshness` and `flow-runbook-freshness` appear
> in two categories. Acceptable — discoverability beats taxonomic
> purity.

**Planning & Process** (6).

`flow-sprint-plan` · `flow-daily-retro` · `role-ba` ·
`role-product-manager` · `role-intake` · `role-developer`

**Reviewers — role personas, standalone** (8).

`role-tech-architect` · `role-qa-architect` · `role-security-officer` ·
`role-designer` · `role-mobile-reviewer` · `role-desktop-reviewer` ·
`role-ml-reviewer` · `role-game-balance-reviewer`

### Critical Plays

Marked `critical: true` in frontmatter (Coverage red badge when
coverage < 100% across activated repos). Final list to confirm
in planning kickoff:

- `flow-pr-self-review`
- `scan-security-deps`
- `scan-license-deps`
- `scan-pii-leakage`
- `flow-incident-postmortem`
- `flow-release-notes`
- `flow-cert-compliance`

---

## §3 · Inbox profiles

Nine profiles cover the catalog; pattern frontmatter references a
profile + optional overrides.

### §3.1 Profile definitions

```yaml
inbox_profiles:

  silent:
    # System-internal, never emits to operator inbox.
    clarification: { enabled: false }
    approval:      { enabled: false }
    failure:       { enabled: false }
    improvement:   { enabled: false }
    exception:     { enabled: false }

  scan_default:
    # Scheduled scanners. Findings = Run only. Human only on dysfunction.
    clarification: { enabled: false }
    approval:      { enabled: false }
    failure:
      enabled: true
      handle: ops_oncall
      when: [play_failed_repeatedly]
    improvement:
      enabled: true
      handle: workspace_owner
      when: [recurring_finding_detected, automation_candidate_detected]
    exception:
      enabled: true
      handle: workspace_owner
      when: [budget_breach_allowance_requested]

  scan_with_autofix:
    # Scanners that propose autofix PRs requiring approval before merge.
    inherits: scan_default
    approval:
      enabled: true
      handle: code_owner
      when: [autofix_proposed, risky_remediation_proposed]

  flow_pr:
    # PR-attached flows. Author owns clarifications, code-owner owns approvals.
    clarification:
      enabled: true
      handle: pr_author
      when: [missing_context, ambiguous_requirement, unclear_change_intent]
    approval:
      enabled: true
      handle: code_owner
      when: [architectural_risk, security_sensitive_change, protected_area_changed]
    failure:
      enabled: true
      handle: repo_maintainer
      when: [play_failed_repeatedly]
    improvement: { enabled: false }
    exception:
      enabled: true
      handle: repo_maintainer
      when: [policy_override_required]

  flow_release:
    # Release-time gates. Approval and exception are first-class.
    clarification:
      enabled: true
      handle: release_manager
      when: [target_release_unclear, missing_release_metadata, version_scope_ambiguous]
    approval:
      enabled: true
      handle: release_manager
      when: [release_blocker_found, conditional_go_decision_required]
    failure:
      enabled: true
      handle: ops_oncall
      when: [play_failed_repeatedly, release_signal_source_unavailable]
    improvement:
      enabled: true
      handle: release_manager
      when: [repeated_release_gap_detected]
    exception:
      enabled: true
      handle: release_manager
      when: [allow_release_with_known_risk, waive_release_policy]

  flow_incident:
    # Incident lifecycle. Incident commander owns clarifications and exceptions.
    clarification:
      enabled: true
      handle: incident_commander
      when: [timeline_gap_detected, missing_incident_artifacts, unclear_decision_owner]
    approval:
      enabled: true
      handle: eng_manager
      when: [corrective_actions_affect_multiple_teams, high_cost_remediation_proposed]
    failure:
      enabled: true
      handle: ops_oncall
      when: [play_failed_repeatedly, incident_data_unavailable]
    improvement:
      enabled: true
      handle: workspace_owner
      when: [recurring_incident_pattern_detected, automation_candidate_detected]
    exception:
      enabled: true
      handle: incident_commander
      when: [unresolved_root_cause, followup_owner_missing]

  flow_reporting:
    # Digest/retro/planning. Inbox traffic is rare.
    clarification:
      enabled: true
      handle: requested_by
      when: [report_scope_unclear, input_sources_missing]
    approval: { enabled: false }
    failure:
      enabled: true
      handle: repo_maintainer
      when: [play_failed_repeatedly]
    improvement:
      enabled: true
      handle: workspace_owner
      when: [repeated_reporting_gap_detected, new_digest_candidate_detected]
    exception: { enabled: false }

  role_reviewer:
    # Generic reviewer persona (security, QA, architect, etc.)
    clarification:
      enabled: true
      handle: pr_author
      when: [missing_context, requirement_unclear]
    approval:
      enabled: true
      handle: code_owner
      when: [risk_threshold_exceeded]
    failure:
      enabled: true
      handle: repo_maintainer
      when: [play_failed_repeatedly]
    improvement: { enabled: false }
    exception: { enabled: false }

  onboarding:
    clarification:
      enabled: true
      handle: workspace_owner
      when: [setup_input_missing]
    approval: { enabled: false }
    failure:
      enabled: true
      handle: workspace_owner
      when: [play_failed_repeatedly]
    improvement: { enabled: false }
    exception: { enabled: false }
```

### §3.2 Workspace handle resolver (per-tenant)

```yaml
version: 1
inbox:
  routing:
    fallback:
      target: group:workspace-owners
      assignment: round_robin

    handles:
      # User/group-backed handles
      workspace_owner:    { target: group:workspace-owners,    assignment: round_robin }
      eng_manager:        { target: group:engineering-managers, assignment: round_robin }
      qa_lead:            { target: group:qa-leads,            assignment: round_robin }
      security_officer:   { target: group:secops,              assignment: round_robin }
      release_manager:    { target: group:release-managers,    assignment: round_robin }
      ops_oncall:         { target: group:ops-oncall,          assignment: oncall }
      incident_commander: { target: group:incident-commanders, assignment: oncall }

      # Strategy-backed handles (compute owner from runtime context)
      pr_author:        { strategy: pr_author }
      code_owner:       { strategy: codeowners }
      requested_by:     { strategy: requested_by }
      repo_maintainer:  { strategy: repo_role,     role: maintainer }
      repo_owner:       { strategy: repo_metadata, field: owner }

  groups:
    workspace-owners:    { members: [denys] }
    engineering-managers: { members: [anna, petro] }
    qa-leads:            { members: [olena, maksym] }
    secops:              { members: [iryna, serhii] }
    release-managers:    { members: [dmytro] }
    ops-oncall:          { members: [oleksii, roman, yuliia] }
    incident-commanders: { members: [oleksii, anna] }
```

### §3.3 Profile assignment for all 77 user-facing Plays

| Profile | Patterns |
|---|---|
| **silent** | `common-base` · `common-kickoff` · `op-retry-sweep` · `op-stale-issue-sweep` · `op-workflow-self-heal` |
| **scan_default** | `scan-a11y` · `scan-asset-budget` · `scan-app-size-budget` · `scan-audit-log-integrity` · `scan-bias-fairness` · `scan-bom-delta` · `scan-build-frametime` · `scan-cost-delta` · `scan-data-drift` · `scan-dead-code` · `scan-docs-freshness` · `scan-env-var-catalog` · `scan-feature-schema` · `scan-firmware-size` · `scan-hal-abi-lock` · `scan-installer-size` · `scan-localization-gap` · `scan-mobile-crash-rate` · `scan-model-eval` · `scan-os-support-matrix` · `scan-performance-budget` · `scan-permissions-audit` · `scan-power-profile` · `scan-sbom-drift` · `scan-slo-health` · `scan-store-metadata` · `scan-tech-debt` · `scan-test-coverage` · `scan-training-repro` |
| **scan_with_autofix** | `scan-api-contract` · `scan-consent-drift` · `scan-iam-policy-diff` · `scan-k8s-policy` · `scan-license-deps` · `scan-pii-leakage` · `scan-security-deps` · `scan-signing-notarization` · `scan-terraform-drift` |
| **flow_pr** | `flow-pr-self-review` · `flow-blast-radius` · `flow-qa-acceptance` · `flow-preview-validation` · `flow-preview-failure-recovery` · `flow-check-failure-recovery` · `flow-human-handoff` |
| **flow_release** | `flow-release-notes` · `flow-store-submission` · `flow-cert-compliance` · `flow-compliance-artifact` · `flow-autoupdate-rollout` · `flow-ota-channel` · `flow-beta-distribution` · `flow-model-card` · `flow-dependency-update` · `flow-live-ops-calendar` |
| **flow_incident** | `flow-incident-postmortem` · `flow-oncall-handoff` · `flow-runbook-freshness` · `role-clarification` |
| **flow_reporting** | `flow-daily-retro` · `flow-sprint-plan` · `flow-learning-capture` |
| **role_reviewer** | `role-tech-architect` · `role-qa-architect` · `role-security-officer` · `role-designer` · `role-mobile-reviewer` · `role-desktop-reviewer` · `role-ml-reviewer` · `role-game-balance-reviewer` · `role-ba` · `role-product-manager` · `role-intake` · `role-developer` |
| **onboarding** | `onboard-adopt` · `onboard-seed-knowledge` |

### Per-pattern overrides to confirm during implementation

- `flow-runbook-freshness` — fits `flow_incident` but `failure` should target `repo_maintainer` (not `ops_oncall`)
- `role-intake` — fits `role_reviewer` but is the *first-contact* persona; `clarification` should target `requested_by` (not `pr_author`)
- `scan-cost-delta` — fits `scan_default` but `improvement` should target `eng_manager` (cost ownership) not `workspace_owner`
- `flow-dependency-update` — currently in `flow_release`; could move to `flow_pr` since it's ongoing maintenance — decide during impl

---

## §4 · Inbox v1 data model — DDL

```sql
-- Operational groups (separate from permission roles)
CREATE TABLE member_groups (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  key           VARCHAR(64)  NOT NULL,                 -- 'secops', 'on-call'
  display_name  VARCHAR(160) NOT NULL,
  description   TEXT,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, key)
);

CREATE TABLE member_group_members (
  group_id  UUID NOT NULL REFERENCES member_groups(id) ON DELETE CASCADE,
  user_id   UUID NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
  added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (group_id, user_id)
);

-- Round-robin pointer per group (so rotation is honest under contention)
CREATE TABLE group_assignment_state (
  group_id              UUID PRIMARY KEY REFERENCES member_groups(id) ON DELETE CASCADE,
  last_assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Workspace-level handle → target mapping
CREATE TABLE inbox_routing_rules (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  handle_key          VARCHAR(64)  NOT NULL,           -- 'security_officer'
  target_type         VARCHAR(16)  NOT NULL,           -- 'group' | 'user' | 'strategy'
  target_value        VARCHAR(160) NOT NULL,           -- group key / user id / strategy name
  assignment_strategy VARCHAR(16),                     -- 'round_robin' | 'oncall' | 'first' | NULL
  strategy_config     JSONB        NOT NULL DEFAULT '{}',  -- e.g. {"role": "maintainer"}
  is_enabled          BOOLEAN      NOT NULL DEFAULT true,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, handle_key)
);

-- The Inbox itself
CREATE TABLE inbox_items (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  repo_id       UUID REFERENCES workspace_repos(id) ON DELETE SET NULL,

  -- Source linkage
  type          VARCHAR(16) NOT NULL,            -- 'clarification'|'improvement'|'failure'|'approval'|'exception'
  source_table  VARCHAR(32),                     -- 'clarifications'|'improvements'|NULL
  source_id     UUID,                            -- weak FK; preserved on source delete
  play_key      VARCHAR(128),                    -- e.g. 'flow-pr-self-review'
  run_id        UUID REFERENCES pipeline_runs(id) ON DELETE SET NULL,

  -- Content
  title         VARCHAR(255) NOT NULL,
  summary       TEXT,
  payload       JSONB        NOT NULL DEFAULT '{}',

  -- Lifecycle
  status        VARCHAR(16) NOT NULL DEFAULT 'new',  -- 'new'|'snoozed'|'resolved'|'dismissed'
  owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  intake_handle VARCHAR(64),                     -- which handle resolved this assignment
  intake_reason TEXT,                            -- 'codeowners:secops'|'fallback'|'round_robin:secops'

  -- Timing
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  due_at              TIMESTAMPTZ,
  snoozed_until       TIMESTAMPTZ,
  resolved_at         TIMESTAMPTZ,
  resolved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  resolution          VARCHAR(32)                  -- 'answered'|'approved'|'rejected'|'accepted'|'dismissed'|'retried'|'acknowledged'
);

CREATE INDEX ix_inbox_owner_status      ON inbox_items (workspace_id, owner_user_id, status);
CREATE INDEX ix_inbox_workspace_status  ON inbox_items (workspace_id, status, created_at DESC);
CREATE INDEX ix_inbox_repo              ON inbox_items (repo_id) WHERE repo_id IS NOT NULL;
CREATE INDEX ix_inbox_run               ON inbox_items (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX ix_inbox_source_lookup     ON inbox_items (source_table, source_id) WHERE source_table IS NOT NULL;

-- Audit trail (every disposition, every reassign, every snooze)
CREATE TABLE inbox_item_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id       UUID NOT NULL REFERENCES inbox_items(id) ON DELETE CASCADE,
  actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  actor_kind    VARCHAR(16) NOT NULL,            -- 'user' | 'system' | 'agent'
  action        VARCHAR(32) NOT NULL,            -- 'created'|'assigned'|'reassigned'|'snoozed'|'unsnoozed'|'resolved'|'dismissed'|'commented'
  payload       JSONB       NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_inbox_events_item ON inbox_item_events (item_id, created_at DESC);

-- Run → Inbox linkage (drives "what did this run produce" view)
CREATE TABLE run_escalations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id            UUID NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  inbox_item_id     UUID NOT NULL REFERENCES inbox_items(id)   ON DELETE CASCADE,
  escalation_reason VARCHAR(64) NOT NULL,        -- e.g. 'play_failed_repeatedly' | 'requires_approval'
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, inbox_item_id)
);
```

### Backfill from existing tables

`clarifications` and `improvements` keep their tables. `inbox_items`
gains rows pointing to them via `(source_table, source_id)`:

```sql
INSERT INTO inbox_items (
  workspace_id, repo_id, type, source_table, source_id, play_key,
  title, summary, payload, status, owner_user_id, created_at, resolved_at
)
SELECT
  c.workspace_id,
  c.repo_id,
  'clarification',
  'clarifications',
  c.id,
  NULL,
  LEFT(c.question, 250),
  c.question,
  jsonb_build_object('ticket_ref', c.ticket_ref, 'context', c.context),
  CASE c.status
    WHEN 'open'     THEN 'new'
    WHEN 'answered' THEN 'resolved'
    WHEN 'skipped'  THEN 'dismissed'
    ELSE 'new'
  END,
  c.answered_by_user_id,
  c.created_at,
  c.answered_at
FROM clarifications c;

-- Improvements analogous:
-- pending → new, accepted/declined → resolved, deferred → snoozed
```

---

## §5 · Inbox state machine

### States

| State | Meaning | Next allowed |
|---|---|---|
| `new` | Created, awaiting first action | snoozed · resolved · dismissed |
| `snoozed` | Hidden until `snoozed_until` | new (auto on timer) · resolved · dismissed |
| `resolved` | Disposition completed positively | — (terminal) |
| `dismissed` | Closed without action (won't-do / dup) | — (terminal) |

### Transitions

| From | To | Trigger | Guard | Side-effect |
|---|---|---|---|---|
| (none) | `new` | `intake.create` | routing resolves owner | events: `created` + `assigned` |
| `new` | `new` | `reassign(user_id)` | owner ≠ target | event `reassigned` |
| `new` | `snoozed` | `snooze(until)` | until > now() | event `snoozed`; clear from `Mine` view |
| `snoozed` | `new` | timer or `unsnooze` | now() ≥ snoozed_until | event `unsnoozed` |
| `new` / `snoozed` | `resolved` | `disposition.X` (Approve/Answer/Accept/Retry/Acknowledge) | type-specific | event `resolved`; write back to source table |
| `new` / `snoozed` | `dismissed` | `disposition.dismiss` | — | event `dismissed`; record reason |

### Disposition matrix

| Type | Allowed dispositions | Resolution value | Side-effect on source |
|---|---|---|---|
| `clarification` | Answer · Reassign · Snooze · Dismiss | `answered` / `dismissed` | UPDATE `clarifications.answer`, `status='answered'` |
| `improvement` | Accept · Decline · Defer · Reassign | `accepted` / `dismissed` | UPDATE `improvements.decision`; if Accept → enqueue Automation creation |
| `failure` | Retry · Disable automation · Acknowledge · Reassign | `retried` / `acknowledged` | optionally trigger pipeline retry; optionally toggle lane `enabled=false` |
| `approval` | Approve · Reject · Request changes · Reassign | `approved` / `rejected` | callback to originating run; payload to agent for resume |
| `exception` | Allow once · Update policy · Pause automation · Reassign | `acknowledged` / `dismissed` | optionally write `workspace_policy` override |

---

## §6 · Migration phases — ticket breakdown

Sized for 1-2 day PRs each. Suffix: `[BE]` backend · `[FE]` frontend ·
`[INFRA]` ops · `[CAT]` catalog.

### Phase 1 — IA rename + collapse (target: 1.5 weeks)

- [ ] **P1-01 [FE]** Add `/automations` and `/runs` routes; render via existing components from `/lanes` and `/pipelines`
- [ ] **P1-02 [FE]** Add `/plays` route merging existing `LibraryCatalog` + `RequestsCatalog` into one grid with category filter (placeholder categories OK)
- [ ] **P1-03 [FE]** Update `app-shell.tsx` `buildWorkspaceNav()` → new IA: `Home · Inbox · Plays · Automations · Runs · Knowledge · Settings`. Inbox route is stub.
- [ ] **P1-04 [FE]** Update `buildRepoNav()` → collapse to `Repo Home · Repo Settings`
- [ ] **P1-05 [FE]** 301 redirects: `/lanes → /automations`, `/lanes?tab=library → /plays`, `/requests → /plays`, `/pipelines → /runs`, `/lanes/[id] → /automations/[id]`, `/pipelines/[pid]/runs/[rid] → /runs/[rid]`
- [ ] **P1-06 [FE]** Delete `/fleet/lanes`, `/fleet/requests` routes; redirect to `/automations?scope=fleet` and `/runs?scope=fleet`
- [ ] **P1-07 [FE]** Delete per-repo `lanes`/`requests` routes; redirect to workspace surfaces with `?repo=<id>`
- [ ] **P1-08 [FE]** Move `/fleet/policy` → `/settings/policy`; `/fleet/adoption` → `/automations?tab=coverage` (stub)
- [ ] **P1-09 [BE]** Add `?scope=fleet|repo|all` and `?repo=<id>` query params to `/v1/workspaces/{id}/lanes` and pipelines endpoints
- [ ] **P1-10 [FE]** Pattern card copy: business name + "Includes N reviews · runs in {mode}"
- [ ] **P1-11 [FE]** Play card CTAs: `Run now` (primary) + `Automate` (secondary). Wire `Run now` to existing one-shot dispatch endpoint.

**DoD:** Old URLs redirect cleanly. New IA renders existing data. No regression in agent dispatch flow. Stale Inbox tab exists with "coming soon".

### Phase 2 — Inbox v1 + routing (target: 3-4 weeks)

- [ ] **P2-01 [BE]** Alembic migration `0031_inbox_v1`: create all §4 tables
- [ ] **P2-02 [BE]** Backfill migration: populate `inbox_items` from `clarifications` + `improvements`
- [ ] **P2-03 [BE]** New routes file `app/api/v1/routes/inbox.py`:
  - `GET /v1/workspaces/{id}/inbox` (filters: type, owner=me|all|user_id, status, repo_id)
  - `GET /v1/inbox/{id}` (detail + events)
  - `POST /v1/inbox/{id}/disposition` (typed action body)
  - `POST /v1/inbox/{id}/reassign`
  - `POST /v1/inbox/{id}/snooze`
- [ ] **P2-04 [BE]** New routes file `app/api/v1/routes/groups.py`: CRUD for `member_groups` and `member_group_members`
- [ ] **P2-05 [BE]** New routes file `app/api/v1/routes/inbox_routing.py`:
  - List/upsert/delete `inbox_routing_rules`
  - `POST /v1/workspaces/{id}/inbox/routing/export` → YAML
- [ ] **P2-06 [BE]** Routing resolver `app/services/inbox/routing.py`:
  - Strategies: `pr_author`, `codeowners`, `requested_by`, `repo_role`, `repo_metadata`
  - `round_robin` (transactional update of `group_assignment_state`)
  - `oncall` (stub for v1; falls through to `round_robin` if no schedule)
- [ ] **P2-07 [BE]** Intake service `app/services/inbox/intake.py`: `create_inbox_item(type, source, payload, context) → inbox_item` (resolves owner, writes events)
- [ ] **P2-08 [BE]** Wire intake into existing flows:
  - Clarifications creation path → also create `inbox_item`
  - Improvements creation path → also create `inbox_item`
  - Pipeline run failure detector → if 3 consecutive failures (or `manual=true`), create `failure` inbox item
  - Run completion handler → if `outcome.requires_approval`, create `approval` inbox item
- [ ] **P2-09 [BE]** Disposition handlers (write-back to source tables, `Accept → create-automation` path)
- [ ] **P2-10 [CAT]** Add `inbox.profile` to all 77 user-facing pattern frontmatters (mapping per §3.3); split into separate PRs by group (scan / flow / role / op+common)
- [ ] **P2-11 [BE]** Profile resolver: pattern frontmatter `inbox.profile` → `inbox_profiles.<name>` lookup with overrides merge
- [ ] **P2-12 [FE]** `/inbox` page: list with filters (`Mine`/`All`/`Unassigned`, type, status), sorted by oldest
- [ ] **P2-13 [FE]** `/inbox/[id]` page: detail + typed disposition buttons + reassign + snooze + audit trail
- [ ] **P2-14 [FE]** Stale badge component (2d yellow / 7d red) on item rows
- [ ] **P2-15 [FE]** `/settings/members` with Members + Groups tabs
- [ ] **P2-16 [FE]** `/settings/inbox-routing` page: rules table + handle resolver preview
- [ ] **P2-17 [FE]** Delete `/clarifications`, `/improvements`, `/r/.../clarifications`, `/r/.../improvements` routes; 301 to `/inbox?type=...`
- [ ] **P2-18 [FE]** Move `/artifact-feedback` → `/settings/catalog-feedback` (admin-only)
- [ ] **P2-19 [INFRA]** Feature flag `inbox_v1_enabled` per workspace; rollout to pilot first

**DoD:** Pilot tenant can: see clarifications + improvements in Inbox · receive auto-assigned items · take all 5 disposition types · reassign · snooze · see audit trail. Routing rules editable in Settings.

### Phase 3 — Outcome-first Runs (target: 2 weeks)

- [ ] **P3-01 [BE]** Extend `pipeline_runs.outcome` JSONB schema (RFC-0010 §RunSummary); validation in API
- [ ] **P3-02 [BE]** `run_escalations` populated whenever intake creates inbox_item with `run_id`
- [ ] **P3-03 [BE]** Update agent run reporters (`shipctl`, in-process patterns) to emit `outcome_text` + structured findings
- [ ] **P3-04 [FE]** Rewrite `/runs` list: outcome-first row layout (`outcome_text` headline + findings counts + escalation badges)
- [ ] **P3-05 [FE]** `/runs/[id]` detail: artifacts grid + escalation deeplinks to inbox items
- [ ] **P3-06 [FE]** Filter chips: by play · by repo · by status · by trigger (`manual`/`scheduled`/`event`) · `has escalations`
- [ ] **P3-07 [CAT]** Update top 10 most-used patterns to populate `outcome_text` properly (rest fall back to default formatter)

**DoD:** Runs list reads as outcomes ("3 issues found · 1 PR opened"), not events. Each run links to its escalations.

### Phase 4 — Plays polish (target: 1 week)

- [ ] **P4-00 [BE]** Aggregation endpoint for Coverage data (`play.assignments_count` per repo, `critical: true` flag)
- [ ] **P4-01 [FE]** Category sidebar on `/plays` (7 categories + Health-checks sub-facets)
- [ ] **P4-02 [FE]** Play detail drawer: business description · what it produces · what's included · default execution mode
- [ ] **P4-03 [FE]** "Last run for this play in this workspace" mini-strip on each card
- [ ] **P4-04 [FE]** After-success banner on run-result page: `→ Run this {play} every Monday automatically` with one-click Automate wizard
- [ ] **P4-05 [FE]** `Coverage` tab in `/automations`: list with progress bars per Q2 spec (sorted by uncovered DESC, critical-uncovered red badge, drill-down to covered/uncovered split + `Apply to all uncovered` CTA)
- [ ] **P4-06 [CAT]** Add `category:` field to all pattern frontmatter (mapping from §2)
- [ ] **P4-07 [CAT]** Mark critical plays with `critical: true` (final list per §2)

**DoD:** Catalog discoverable via categories. Coverage view drives automation creation. After-run banner converts manual runs into automations.

### Phase 5 — Onboarding wizard v2 (DONE)

Drop preset radio (`web-app` / `non-web-app` / …); ship a single
canonical bootstrap PR per repo + dispatch initial knowledge harvest
+ seed Inbox routing from `CODEOWNERS` + create synthetic Lanes
immediately so the new IA isn't empty after onboarding.

- [x] **P5-01 [BE]** Collapse 14 legacy presets to one canonical `default`; `normalize_preset()` for back-compat
- [x] **P5-02 [BE]** `app/services/codeowners.py` — fetch / parse / resolve `@handle` → `user_id` against workspace membership
- [x] **P5-03 [BE]** `repo_intel` table + `harvest_repo_intel()` service + `arq` background job
- [x] **P5-04 [CAT]** `DEFAULT_BUNDLE` (7 core Plays) + `DEFAULT_BUNDLE_REASONS` for UI explanations
- [x] **P5-05 [BE]** Rewrite `compose_seed_files()` — no preset branching; emit `.ship/config.yml` + workflows + knowledge starters + `.ship/state/wizard-seed.v2.json` marker
- [x] **P5-06 [BE]** `wizard_seed` orchestration v2: compose → seed routing from CODEOWNERS → enqueue intel harvest → `synthetic_lane_sync` → `WizardSeedOut` extended
- [x] **P5-07 [BE]** Synthetic Lane rows on wizard seed (`lanes.origin`); reconciled to `merged` on real PR merge
- [x] **P5-08 [FE]** Wizard rewrite — drop preset radio · steps `github` → `repos` → `tracker` → `confirm` → `done` · render `DEFAULT_BUNDLE` preview
- [x] **P5-09 [FE]** Post-bootstrap "What just happened" page — repo result cards · CODEOWNERS preview · intel-poll badge · "What's next" CTA grid
- [x] **P5-10 [TEST]** e2e wizard happy path covers full new flow with `data-testid` hooks

**DoD reached:** New repos get one PR with full SDLC bundle · CODEOWNERS-driven Inbox routing pre-seeded · intel harvest dispatched · synthetic Lanes visible immediately · "what just happened" page bridges wizard → daily IA.

---

### Phase 6 — Navigator tools (target: 1 week)

Expose every new surface from P1–P5 to the in-product Navigator
(C12 chat at `/chat`) so the agent can drive Inbox / Plays /
Automations / Runs / Coverage / Knowledge instead of the user
hunting through pages. Implementation = extend the existing
`ToolBox` (`backend/app/services/agent/tools.py`) — no new MCP
runtime needed.

**Architecture decision:** keep tools in-process inside the chat
turn (auth, workspace scoping, audit, RBAC re-use the same
`AsyncSession` and `AuthContext`). MCP-server export is **out of
scope**; revisit if/when an external agent needs it.

#### Wave A — Read-only surfaces (parallelizable)

- [ ] **P6-01 [BE]** `inbox_list` / `inbox_counts` / `inbox_get` — read tools over `/v1/.../inbox*` (filters: type, status, owner=me|all|user_id, repo_id, play_key, limit)
- [ ] **P6-02 [BE]** `inbox_routing_list` / `inbox_routing_preview` — list rules + dry-run resolver against a sample item; never mutates
- [ ] **P6-03 [BE]** `plays_coverage` — wrap `GET /plays/coverage`; supports `category`, `critical_only`, `has_gaps`; returns rows with `repos_uncovered` deeplinks
- [ ] **P6-04 [BE]** `plays_list` / `plays_get` — catalog enumeration with category + critical filters; richer than existing `list_catalog_artifacts`
- [ ] **P6-05 [BE]** `runs_query` — outcome-first list; filters by play, repo, status, trigger, `has_escalations`, time window; returns `outcome_text` + `findings_by_severity`
- [ ] **P6-06 [BE]** `run_detail` — artifacts + findings + escalations as deeplinks to inbox items
- [ ] **P6-07 [BE]** `automations_list` — pipelines + lanes + fleet-lanes consolidated; `scope=all|fleet|repo`
- [ ] **P6-08 [BE]** `repo_intel_get` — current `repo_intel` snapshot (languages, frameworks, structure, commit style, visual tokens)
- [ ] **P6-09 [BE]** `knowledge_search_v2` — extend `search_workspace_kb` semantics with explicit `bucket_slug`, `repo_id`, and `intel_facts: bool` flag that augments hits with `repo_intel` summary

#### Wave B — Mutating surfaces (sequential, audit-heavy)

- [ ] **P6-10 [BE]** `inbox_dispose` — typed disposition (`accept`/`reject`/`snooze`/`reassign`/`comment`); `dry_run: bool` for preview; writes `audit_events`
- [ ] **P6-11 [BE]** `inbox_snooze` / `inbox_reassign` — explicit single-purpose tools (LLMs pick these over polymorphic dispose for trivial cases)
- [ ] **P6-12 [BE]** `play_run_now` — manual dispatch on a Play+repo; admin-gated; mirrors `POST /pipelines/{id}/runs`
- [ ] **P6-13 [BE]** `play_automate` — create automation (Lane) for Play × scope × cadence; admin-gated
- [ ] **P6-14 [BE]** `automation_toggle` — enable/disable a pipeline; admin-gated; audit
- [ ] **P6-15 [BE]** `intel_harvest_trigger` — re-run harvest for a repo; admin-gated; rate-limited (1/hr/repo)
- [ ] **P6-16 [BE]** `inbox_routing_upsert` — create or modify a routing rule; admin-gated; audit

#### Wave C — Prompt + UX glue

- [ ] **P6-17 [PROMPT]** Update `_AGENT_SYSTEM_PROMPT` in `topic.py` with: tool-selection guidance for the new IA · "prefer Inbox dispose over create_ticket when an item exists" · "use plays_coverage to answer 'what's missing?'" · examples
- [ ] **P6-18 [BE]** Tool-level RBAC: each mutating tool calls a shared `_require_workspace_role(ROLES_ADMIN)`; reads default to `ROLES_READ`
- [ ] **P6-19 [BE]** Audit envelope: every mutating tool writes `AuditLog(event="navigator.tool.<name>", actor=user_id, payload=arguments_redacted)`
- [ ] **P6-20 [FE]** Render `inbox_dispose` / `play_run_now` / `automation_toggle` results as **action cards** (not bare JSON) in `single-window-chat.tsx` — a small per-tool registry mapping `tool_name → renderer`
- [ ] **P6-21 [FE]** "Open in Inbox" / "Open in Runs" deeplink chips on tool result cards (uses `repo` and `id` from tool output)
- [ ] **P6-22 [BE]** Lower chat RBAC from `ROLES_ADMIN` to `ROLES_MEMBER` for the `/chat/stream` route — Navigator should be available to all workspace members; mutating tools still admin-gated per P6-18 (separate concern)

#### Wave D — Tests + docs

- [ ] **P6-23 [TEST]** Unit tests per new tool (mock LLM, assert tool dispatch, response shape, RBAC denial path) — `backend/tests/test_navigator_*.py` family
- [ ] **P6-24 [TEST]** Integration test: full chat turn that calls `inbox_list` → `inbox_dispose` and asserts side-effects (`InboxItem.status`, `AuditLog`)
- [ ] **P6-25 [DOCS]** New page `documentation/internal/navigator-tools.md` — tool inventory by category · auth/RBAC table · "how to add a new tool" recipe

**Parallelization plan:**
- Wave A (P6-01..09) — 3 sub-agents, each owns 3 read tools; no shared state.
- Wave B (P6-10..16) — must follow A (touches the same `tools.py`); 2 sub-agents on disjoint tool clusters.
- Wave C (P6-17..22) — prompt + FE rendering + RBAC; depends on B.
- Wave D (P6-23..25) — runs after C; can be parallel.

**DoD:** Navigator can list / dispose Inbox items, query coverage gaps, run a Play, automate a Play, and explain repo intel — all from one chat. Member-tier users have access; mutations are admin-gated and audited.

---

### Phase 7 — Documentation rewrite (target: 3-5 days)

Move the **operator-facing** Manual to **Plays / Automations / Runs /
Inbox**. Keep the protocol corpus (RFCs, `lanes:` config schema)
authoritative — those terms remain inside `.ship/config.yml` and
`shipctl`; what changes is the **vocabulary the operator sees** in
the Manual + the **landing sidebar**. Catalog `ARTIFACT.md` bodies
are deferred to a separate batched epic (122 files, not blocking).

#### Wave A — High-impact operator pages

- [ ] **P7-01 [DOC]** `landing/src/lib/docs-nav.ts` — replace "Lanes" sidebar entry with **Automations**; add **Plays**, **Inbox**, **Runs** entries; preserve existing slugs as 301-friendly aliases where possible
- [ ] **P7-02 [DOC]** `documentation/index.md` — refresh Manual home: new vocabulary box (RFC-0010 reference), replace `/lanes` mentions with **Automations** / **Plays**, add **Inbox** + **Navigator** prominence
- [ ] **P7-03 [DOC]** `documentation/concepts.md` (20K) — flagship vocabulary page: introduce Plays / Automations / Runs / Inbox as primary nouns; demote Lanes / Pipelines / Requests to "internal config terms" boxes; add §Glossary mirroring RFC-0010
- [ ] **P7-04 [DOC]** Rename + rewrite `documentation/lanes.md` → `documentation/automations.md` (largest debt, 106 "lane" hits): operator mirror of `/automations` console page; explain Coverage tab; reference `lanes:` config as the underlying mechanism; keep `lanes.md` as a redirect stub `> Renamed to [Automations](./automations.md)`
- [ ] **P7-05 [DOC]** `documentation/knowledge-buckets.md` — replace "Catalog, Clarifications, Improvements" enumeration with **Inbox types**; align Navigator section with the new tools (link to internal `navigator-tools.md`)
- [ ] **P7-06 [DOC]** `documentation/configuration.md` — keep `lanes:` schema (still authoritative inside `.ship/config.yml`) but add a **glossary header**: "in the console, `lanes:` entries appear as **Automations**, the `pattern:` they reference is the **Play**"; rewrite intro paragraphs

#### Wave B — Secondary surfaces

- [ ] **P7-07 [DOC]** `documentation/operating.md` — day-2 surface: Inbox-first runbook for "what's broken / what needs my attention", coverage-driven adoption section
- [ ] **P7-08 [DOC]** `documentation/troubleshooting.md` — update routing diagrams + URL references; add Inbox routing troubleshooting section
- [ ] **P7-09 [DOC]** `documentation/discovery.md` — refresh "first day" narrative: open Inbox first, then Plays catalog
- [ ] **P7-10 [DOC]** `documentation/authoring.md` — keep pattern-author terminology (the contributor still authors **patterns** in the catalog), but add a "Author's view vs Operator's view" callout box — "what you author as a `pattern` appears to operators as a **Play**"
- [ ] **P7-11 [DOC]** `documentation/authoring/pattern-vs-knowledge.md` — small refresh, mostly cross-link updates
- [ ] **P7-12 [DOC]** `documentation/agent-matrix.md` — refresh agent role descriptions to use Inbox vocabulary
- [ ] **P7-13 [DOC]** `documentation/protocol/index.md` — promote RFC-0010 to "Accepted" if status is right; ensure the new IA is the documented end state

#### Wave C — Validation + cleanup

- [ ] **P7-14 [DOC]** `landing` build green, no broken intra-doc links (`grep -r "(./lanes)" documentation/`)
- [ ] **P7-15 [DOC]** Add a `documentation/CHANGELOG.md` (or single section in `index.md`) noting "Sept 2026 — IA refresh: Lanes/Pipelines/Patterns are now Automations/Runs/Plays in the operator surface"

#### Out of scope for Phase 7

- **122 catalog `ARTIFACT.md` bodies** — deferred to a separate batched pass (templated find-replace + per-pattern review). Not a blocker for the Manual to read correctly.
- **Landing pages** (`/`, `/use-cases/*`, `/getting-started`) — Phase 9.
- **The internal "book"** (`landing/content/book.md`) — not committed to repo currently; punt to Phase 9 or whenever it's restored.
- **CLI docs** (`cli/README.md`) — Phase 8.

#### Parallelization plan

- Wave A — one sub-agent (5 markdown files + 1 TS file; needs coherent voice across all)
- Wave B — one sub-agent (5 secondary pages; lower stakes)
- Wave C — me, with a build + grep sweep

**DoD:** Manual reads coherent in new IA on first encounter. `lanes.md`
redirects to `automations.md`. Sidebar shows new IA. Internal `lanes:`
config term remains correct in `configuration.md` with operator
mapping callout. Build is green.

---

### Phase 8 — CLI audit & docs (target: 2-3 days)

Audit every `shipctl <X>` subcommand against the new IA, fix actual
bugs uncovered (duplicate dispatch, missing `--help`, stale doc
claims), rewrite `cli/README.md` to document the Phase-3 `callback`
outcome flags + Phase-7 IA vocabulary, and refresh the landing
`/cli` page + setup wizard. **Do NOT rename `lanes:` or `--lane`** —
those are protocol-stable inside `.ship/config.yml`. Vocabulary lift
is operator-prose only ("`lanes:` entries appear as Automations").

#### Wave A — Source-side hygiene + help text

- [ ] **P8-01 [CLI]** `cli/bin/shipctl.mjs` — remove duplicate `doctor` dispatch block (lines 71-74 vs 88-92); de-dup test
- [ ] **P8-02 [CLI]** `cli/lib/commands/sync.mjs` — add `printSyncHelp()` + `--help` handler; document every flag from `parseSyncArgs`; warn on unknown tokens (currently dropped silently)
- [ ] **P8-03 [CLI]** `cli/lib/commands/help.mjs` — refresh top-level `printHelp()`: add IA vocabulary preamble (Plays/Automations/Runs/Inbox + mapping line), surface `callback` outcome flags briefly, group commands logically (Setup / Catalog / Run / Telemetry / Misc), drop "RFC-0001" framing in favour of operator-first
- [ ] **P8-04 [CLI]** Sweep `ship <verb>` → `shipctl <verb>` in `cli/lib/commands/{docs,search,patterns,manifest-catalog}.mjs` help strings; covered already by `init-help.test.mjs` for the top-level surface, extend test to spot-check each command
- [ ] **P8-05 [CLI]** `cli/lib/commands/run.mjs` — clarify in help that `kind: lane` / `event` / `schedule` lanes execute via the workspace runner (`run-agent.yml`), not `shipctl run` directly; the `kind: once` noop message is correct but cryptic
- [ ] **P8-06 [CLI]** `cli/lib/commands/callback.mjs` — keep flags identical (protocol stable) but rewrite help prose to reference Inbox / Run / RunSummary terminology; add a short example block at the bottom
- [ ] **P8-07 [CLI/TEST]** Snapshot tests for `shipctl <cmd> --help` for the 8 most-used commands (init / sync / verify / config / lanes / run / callback / doctor) — guards against accidental regressions in help copy

#### Wave B — `cli/README.md` rewrite

- [ ] **P8-08 [DOC]** Add a top-level **Vocabulary** callout (same style as the Manual): `lanes:` ↔ Automations, `pattern:` ↔ Play, `pipeline_runs` ↔ Run, "what needs you" ↔ Inbox
- [ ] **P8-09 [DOC]** Remove every `workflow` subcommand reference (lines ~81, ~206); the kind was removed in Phase 6
- [ ] **P8-10 [DOC]** Add a missing **`shipctl run`** section between Sync and Verify: lane dispatch, multi-pattern, `--repo` fanout, callback envs, exit codes
- [ ] **P8-11 [DOC]** Add a missing **`shipctl lanes`** section: `install`/`list`/`remove`, what it writes to `.github/workflows/`, relationship to console Automations, fleet-vs-repo scope
- [ ] **P8-12 [DOC]** Add a missing **`shipctl callback`** section: when patterns call it (the `## Reporting` block from P3-07), the **RunSummary outcome flags** (`--outcome-text`, `--findings-count`, `--severity`, `--artifact`, `--escalation`, `--requires-approval`), env-vs-flag merge order, common pattern recipes
- [ ] **P8-13 [DOC]** Add a short **`shipctl knowledge init`** section (currently only mentioned in passing)
- [ ] **P8-14 [DOC]** Refresh the Quick reference table at L199 — every command + one-line blurb + which Manual section explains it
- [ ] **P8-15 [DOC]** Strike the "RFC-0001 artifacts protocol" framing from the intro; replace with operator-first "Ship's CLI does three things: bootstrap a repo, sync the catalog, and run lanes"

#### Wave C — Landing + setup wizard + Manual fixes

- [ ] **P8-16 [FE]** `landing/src/app/cli/page.tsx` — extend the static `COMMANDS` array with `run`, `lanes`, `callback`; refresh the existing 5 blurbs to reflect IA vocabulary
- [ ] **P8-17 [FE]** `landing/src/app/getting-started/page.tsx` + `landing/src/components/agent-setup-form.tsx` — vocabulary refresh ("after first sync, open the Inbox to see what needs your attention; visit Plays to browse the catalog; Coverage will show gaps"); regenerate any example `shipctl init` with current flag surface
- [ ] **P8-18 [DOC]** Fix `documentation/automations.md` ~L141 stale claim that `shipctl run` requires single-pattern lane (it's been multi-pattern for a while; `run.test.mjs` proves it)
- [ ] **P8-19 [DOC]** Sweep `landing/content/blog/*.md` for `shipctl fetch` / `shipctl adopt` references that don't match the binary; either fix or add a "blog post pre-dates the current CLI" note inline
- [ ] **P8-20 [CLI]** **(soft alias)** Register `shipctl automations` as alias → same dispatch as `shipctl lanes` in `bin/shipctl.mjs`; print a one-line "(alias of `shipctl lanes`; both work)" in its help. No deprecation of the original — both ship indefinitely
- [ ] **P8-21 [CLI]** `cli/package.json` — refresh `description` field if it still mentions workflows; bump patch version
- [ ] **P8-22 [DOC]** `documentation/CHANGELOG.md` — add CLI section noting the new `--help` coverage, the `automations` alias, and the `callback` outcome documentation

#### Out of scope for Phase 8

- **Removing** `--lane` flag or `lanes` subcommand (would break shipped workflows; protocol-stable).
- **Removing** `pattern` as a subcommand (it's the artifact kind from RFC-0001/0008; protocol-stable).
- **Generating** help text from a schema — current hand-maintained pattern is fine for 20 commands; revisit only if surface grows.
- **`shipctl bootstrap` stub** — leave for a future cleanup pass.

#### Parallelization plan

- Wave A — one sub-agent (touches many files but all small, single coherent voice for help text)
- Wave B — me (markdown rewrite, full control of tone)
- Wave C — one sub-agent (landing FE + manual sweep + alias)

**DoD:** `cli/README.md` documents every command including `callback` outcome flags. `shipctl help` reads operator-first. Landing `/cli` lists at least the 8 most-used commands. The duplicate doctor dispatch is gone. `automations` alias works. No remaining `workflow` subcommand mentions in any doc surface.

---

### Phase 9 — Landing page (target: 2 days)

Refresh the public landing site to communicate the **Plays / Automations
/ Runs / Inbox** operator model that shipped through Phases 1-8, retire
the few remaining references to the obsolete `workflow` artifact kind
(removed in Phase 6), and unstale the `v0.7.0` hero badge. **Do NOT
rewrite the blog or the book** — those are historical record (the blog
posts that needed editorial notes already got them in Phase 8). **Do
NOT reshoot screenshots** — defer asset refresh to a separate follow-up
unless something is materially wrong on disk; the operator console
itself is still evolving. **Do NOT break protocol-stable terms in CLI
copy** (`lanes:` / `--lane` / `pattern:` / `shipctl pattern` stay
literal).

#### Wave A — Home page narrative

- [ ] **P9-01 [FE]** `landing/src/components/hero-section.tsx` — drop the literal `v0.7.0` badge in favour of pulling from `package.json` at build time (or hardcode current `0.11.2`); rewrite the eyebrow + lede to mention the operator loop ("Plays you assign as Automations, Runs you watch, an Inbox that catches what needs you")
- [ ] **P9-02 [FE]** `landing/src/components/how-it-works-section.tsx` — extend beyond init/sync/verify to a 5-step operator loop: **Bootstrap** (`shipctl init`) → **Pick Plays** → **Assign as Automations** → **Watch Runs** → **Triage Inbox**. Each step gets a 2-3 line blurb + matching `shipctl` / console deeplink
- [ ] **P9-03 [FE]** New `landing/src/components/operator-loop-section.tsx` — single section that explains the four nouns the same way `documentation/concepts.md` does, with a tiny "Vocabulary at a glance" card. Composed into `app/page.tsx`. Marketing-grade tone (not docs)
- [ ] **P9-04 [FE]** `landing/src/components/patterns-section.tsx` — strip "tools, workflows, and collections" framing (workflows kind retired Phase 6). Replace with "patterns / tools / collections" and frame patterns as **the source of Plays**
- [ ] **P9-05 [FE]** `landing/src/components/kit-surface-section.tsx` — replace "lane playbooks" tile copy with "Automations" prose; mention the new operator surfaces
- [ ] **P9-06 [FE]** `landing/src/components/backend-strip.tsx` — drop "tools, workflows, and collections"; clarify that `improvement notes` is what feeds the Inbox
- [ ] **P9-07 [FE]** `landing/src/components/site-footer.tsx` — remove "workflows" from the kit blurb; cross-link `/docs/concepts` for vocabulary
- [ ] **P9-08 [FE]** `landing/src/app/page.tsx` — wire the new `OperatorLoopSection` between `HowItWorksSection` and `CommandBuilderSection`

#### Wave B — Catalog + use-cases vocabulary

- [ ] **P9-09 [FE]** `landing/src/app/patterns/page.tsx` — rewrite metadata + body away from "lane playbooks"; frame patterns as "the source of Plays in your operator console"
- [ ] **P9-10 [FE]** `landing/src/components/patterns-catalog.tsx` — rename the **"Lane prompts"** tab to **"Automation patterns"** (display label only — the underlying group id `lanes` stays for protocol compat)
- [ ] **P9-11 [FE]** `landing/src/lib/patterns.ts` + `landing/src/lib/artifacts-fs.ts` — keep the `PatternGroup` type literal `"lanes"` (it maps to YAML `lanes:`); only refresh prose in summary strings
- [ ] **P9-12 [FE]** `landing/src/app/collections/page.tsx` — replace "workflow intents" in metadata description (workflow kind is gone)
- [ ] **P9-13 [FE]** `landing/src/app/kit/page.tsx` — same "lane playbooks" → Automations sweep, plus tile cross-links if any
- [ ] **P9-14 [FE]** `landing/src/app/use-cases/page.tsx` — rewrite hero "delivery-lane drift" + "named workflows" framing; lead with "Plays they ship, Runs they watch, the Inbox that catches the rest"
- [ ] **P9-15 [FE]** `landing/src/app/use-cases/elmundi/page.tsx` — title + intro updated to match the new framing; **keep** GitHub Actions "workflows" references intact (those mean GHA, not the retired artifact kind)
- [ ] **P9-16 [FE]** `landing/src/app/use-cases/ship/page.tsx` — light vocabulary sweep; "workflow names in CI" can stay (means GHA file names)

#### Wave C — Hygiene + SEO + CHANGELOG

- [ ] **P9-17 [FE]** Add `landing/src/app/sitemap.ts` — generate sitemap from the static route list + dynamic blog/docs/pattern/tool/collection slugs
- [ ] **P9-18 [FE]** Add `landing/src/app/robots.ts` — allow all, point at sitemap
- [ ] **P9-19 [FE]** `landing/src/app/layout.tsx` — refresh global `metadata.description` to mention the operator nouns; add `openGraph.images` only if a non-stale image exists in `public/`, otherwise leave as-is
- [ ] **P9-20 [FE]** `landing/src/lib/docs-nav.ts` — final marketing-vs-docs voice consistency pass
- [ ] **P9-21 [FE]** Asset orphan check — confirm `public/landing/hero-methodology-kit.png` is referenced (the report flagged it as possibly orphaned); if truly orphaned, remove
- [ ] **P9-22 [DOC]** `documentation/CHANGELOG.md` — append "Phase 9 — Landing page" section (Operator loop section, hero unstaling, vocabulary alignment, sitemap/robots)

#### Out of scope for Phase 9

- **Blog rewrite.** Phase 8 added editorial notes where the prose was actively misleading. The rest is Ship Log / build-in-public — historical.
- **Book rewrite.** `landing/content/book.md` is not currently in the workspace; `/book` shows a fallback. Reconciling `sync-book.mjs` vs `build-book-pdf.mjs` is a separate follow-up.
- **Screenshot refresh.** The console is still evolving; reshoot when it stabilizes for a release. Old PNGs are stale-ish but not actively wrong.
- **Pricing / testimonials / new pages.** No new routes — refresh only.
- **Renaming protocol-stable terms** anywhere — `lanes:` / `--lane` / `pattern:` / `shipctl pattern` stay literal in code, YAML, CLI flags.

#### Parallelization plan

- **Wave A** — me (single coherent voice for the home rewrite; new `OperatorLoopSection` component depends on the rest)
- **Wave B** — one sub-agent (catalog + use-case vocabulary sweep, mostly mechanical)
- **Wave C** — one sub-agent (sitemap/robots/SEO/CHANGELOG)

**DoD:** Home page mentions Plays / Automations / Runs / Inbox by name and links to `/docs/concepts`. Hero version badge is current. Every "tools, workflows, and collections" parallel-listing is gone. Patterns catalog tab is "Automation patterns". Use-cases pages lead with the new framing. Sitemap + robots ship. Landing build clean.

### Phase 10 — External channels (parked, OUT of current scope)

Email · Slack · Teams · PR comment ingestion. Each = separate
adapter + auth + threading. Tracked separately when there's pull.

---

## §7 · Risks register

| # | Risk | Phase | Mitigation |
|---|---|---|---|
| R1 | Pilot tenant has live `clarifications`/`improvements` data; backfill drift between read (inbox) and write (legacy tables) creates ghost items | P2 | Wrap legacy create endpoints to write both sides atomically; nightly reconciliation job; monitoring |
| R2 | Routing resolver fails (CODEOWNERS API down, repo metadata stale) → items end up at fallback only → fallback owner buried under unrelated traffic | P2 | Resolver returns `intake_reason` always; failed strategies logged; UI shows "couldn't resolve `code_owner`, fell back — fix?" with deeplink to routing rule |
| R3 | Round-robin pointer race (two intakes pick same user) | P2 | `group_assignment_state` updates wrapped in `SELECT FOR UPDATE`; transactional with item insert |
| R4 | Pattern profile bulk update touches 77 files at once → catalog PR is unreviewable | P2/P4 | Split CAT tickets by group: scan-* (PR), flow-* (PR), role-* (PR), op/common (PR) |
| R5 | Composite Plays not yet defined; implementation might force new YAML schema | P4 | Defer composites to Phase 4.5; v1 catalog Plays all map 1:1 to single pattern; flag composites as Phase 5 prep |
| R6 | `requires_approval` flow breaks because patterns don't currently know how to set it | P3 | Roll out P3 with one pilot pattern (`scan-license-deps` autofix); validate end-to-end before mass adoption |
| R7 | Repo Home becomes the new "kitchen sink" surface as PMs ask for more widgets | P1/P4 | Lock the 4-widget set in §1 as design contract; new widgets require explicit ADR |
| R8 | Permission roles vs operational groups confusion in UI ("why can't I assign to this admin?") | P2 | Settings copy: "Roles control what people CAN do. Groups control what people SHOULD do." Both visible on each member row. |
| R9 | Old URL bookmarks → 404 storm if redirects miss a case | P1 | 301 with `?from=legacy_<route>` query for telemetry; monitor 404s for 30 days post-launch |
| R10 | Coverage view requires aggregations that don't exist | P4 | P4-00 prerequisite ticket for backend aggregation endpoint |

---

## §8 · Definition of Done — per phase

| Phase | DoD checklist |
|---|---|
| P1 | Old URLs return 301 to new ones · sidebar shows new IA · `/plays`, `/automations`, `/runs` render existing data with new naming · Inbox tab exists as stub · pilot tenant smoketest passes |
| P2 | Inbox tab shows real items · routing rules CRUD works · all 5 disposition types tested end-to-end · audit trail visible · `clarifications`/`improvements` legacy routes redirect · feature flag default-on for pilot |
| P3 | Runs list shows outcome sentences for top-10 patterns · run detail links to inbox items · all old triggers/filters work in new view |
| P4 | All Plays categorized · Coverage view shipped · after-success Automate banner functional · pattern frontmatter linted to require `category` and `inbox.profile` |

---

## §9 · Day-1 action items (right after planning kickoff)

1. **[BE]** Open RFC PR for §4 schema + §3 YAML — get backend lead sign-off before P2 starts
2. **[CAT]** Bulk PR adding `inbox.profile` to all 77 patterns (mapping from §3.3) — purely additive, mergeable independently
3. **[FE]** Create `/inbox` route stub + sidebar entry — unblocks design iteration
4. **[INFRA]** Add `inbox_v1_enabled` feature flag to pilot tenant config
5. **[CAT]** Triage 4 edge-case patterns from §3.3 → final calls
6. **[PM]** Lock category mapping & critical-play list (§2)
7. **[design]** Wireframes for Inbox list, Inbox detail, Settings/Routing — 3 net-new screens

---

## §10 · Out of scope (explicit list)

- Email / Slack / Teams ingestion
- Inbox SLA matrix · escalation chains beyond stale badge
- Workspace-defined composite Plays
- Multi-owner inbox items / shared queues
- Visual rule builder for routing
- Per-Play policy override editor surfaced to operators
- Inbox item nested threads / replies
- Inbox cross-workspace queries
- Mobile-optimized Inbox UI
- Coverage matrix / heatmap (deferred to v2)

---

## §11 · Glossary

| Term | Definition |
|---|---|
| **Play** | A ready-made operational procedure in the catalog. Atomic or composite. |
| **Automation** | A Play assigned to a scope with a cadence. |
| **Run** | A single execution of a Play (manual or automated). |
| **Inbox item** | A work item requiring human disposition. Always has one owner. |
| **Handle** | A symbolic role declared by a Play; resolved by workspace routing rules. |
| **Group** | A workspace-level operational set of users. Distinct from permission role. |
| **Disposition** | A typed action that resolves an Inbox item. |
| **Coverage** | How many activated repos have a given Play assigned. |
| **Lane** | *(internal)* Persisted record in `.ship/config.yml` for an Automation. Not user-facing. |
| **Pattern** | *(internal)* Atomic executable definition (markdown + frontmatter). Not user-facing. |
| **Workflow** | *(internal)* Orchestration mode of a composite Play (`parallel` / `sequential` / `separate_workflows`). Not user-facing. |
