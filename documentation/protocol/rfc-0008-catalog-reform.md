---
rfc: 0008
title: "Catalog reform — naming, modes, and expansion"
status: Draft
created: 2026-04-22
supersedes_in_part: []
---

# RFC-0008 — Catalog reform: naming, modes, and expansion

## Abstract

The pattern catalog accreted three naming schemes (`cloud-*`,
`catalog-a<N>-*`, verb-first `adopt-ship-*`, bare `kickoff`), carries no
metadata about *how* a pattern is meant to be invoked (scheduled lane,
event-driven lane, ad-hoc request, or a mix), and the Lane-vs-Request
UI surfaces both pull from different sources (the former from
`DefaultPipelineSpec`, the latter from free-form text). Operators who
land in Requests can't pick from a catalog; operators who land in Lanes
see only five baked-in recipes.

This RFC:

1. **Unifies the catalog as the single source of truth** for all
   scheduled/event/ad-hoc workloads and retires `DefaultPipelineSpec`.
2. **Renames every pattern** to `<category>-<name>` with six canonical
   categories (`role`, `flow`, `scan`, `op`, `onboard`, `sys`).
3. **Adds trigger/invocation metadata** (`category`, `modes`,
   `default_trigger`, `inputs`, `enabled_on_install`) to each pattern.
4. **Filters the catalog by mode** in the UI — Lanes Library shows
   `modes: [lane]` patterns, Requests shows `modes: [request]`, and
   `both` appears in both.
5. **Expands the catalog** with 10 new Phase-1 patterns (scanners,
   release flows, ops) that cover the most common operator asks.
6. **Clean break** on the rename — no aliases, no transition period
   (no external users depend on pattern ids yet).

RFC-0007 introduced `shipctl run --lane <id>` as the single dispatch
entry point. This RFC completes the unification by making the Requests
pathway `shipctl run --pattern <id>` — a pattern the operator picks
from the catalog rather than a prompt they type.

## Implementation status

| Phase | Scope                                                        | Status  |
|-------|--------------------------------------------------------------|---------|
| 0     | Schema contract (parse `category` / `modes` / `default_trigger` / `inputs` / `enabled_on_install`). | shipped — commit `e8e6a26` on 2026-04-22 |
| 1     | Pattern rename to `<category>-<name>`.                       | shipped — commit `e8e6a26` on 2026-04-22 |
| 2     | Retire `DefaultPipelineSpec` (C3.1–C3.4).                    | planned next |
| 3     | Requests catalog UI.                                         | partial — landed in the 2026-04-22 Lanes hub + Requests 3-phase overhaul (commit `0506ae9`); pattern-driven form is live, full pattern-input schema still landing |
| 4     | Expansion pack — 10 new Phase-1 patterns.                    | landed |

## Motivation

Three concrete problems:

1. **Naming heterogeneity hurts discoverability.** `cloud-ba` is a
   role pattern from the "cloud-agent" family; `catalog-a1-intake` is
   the same role as `cloud-intake` but numbered; `adopt-ship-generic`
   is verb-first. A new operator opening the catalog can't tell what
   any of them does from the id alone.
2. **No modality metadata means Lanes and Requests pull from
   different worlds.** `DefaultPipelineSpec` is a fixed 5-tuple
   (`pr_review`, `daily_standup`, `tech_debt`, `self_heal`,
   `code_map`) — nothing to do with patterns. Meanwhile Requests lets
   operators type free-form prompts because there's no way to declare
   "this pattern is a valid one-shot request."
3. **Gaps in coverage push operators to hand-roll.** No `scan-*`
   patterns (tech-debt scanner lives as a workflow id with no reusable
   prompt). No `flow-release-notes`, no `flow-incident-postmortem`.
   Operators either invent prompts in the Requests freeform field or
   skip automation altogether.

## Terms

- **Pattern** — a versioned `ARTIFACT.md` carrying an agent prompt.
- **Category** — the logical group a pattern belongs to. One of
  `role` / `flow` / `scan` / `op` / `onboard` / `sys`.
- **Mode** — the invocation shape a pattern supports. One of `lane`
  (scheduled or event-driven, wired from `.ship/config.yml`),
  `request` (ad-hoc one-shot dispatched from the Requests UI), or
  both. `common-*` patterns carry `modes: []` — they're not directly
  invokable, only included by other patterns via `spec.include`.
- **Include** — a pattern id listed under `spec.include: [...]` whose
  body is composed into the host pattern's prompt at render time.
  Canonical way to share boilerplate (safety rules, Ship callback
  glue, tracker FSM primer).
- **Default trigger** — the pattern's suggested lane wiring
  (schedule cron or event filter), surfaced in the Library UI as the
  pre-filled default when the operator clicks "Add".
- **Inputs** — named parameters the pattern accepts when dispatched
  as a request (drives the Requests form).

## Naming convention

Every pattern id matches `^[a-z]+-[a-z0-9-]+$` with the first segment
being one of the six canonical categories (`role`, `flow`, `scan`,
`op`, `onboard`, `common`).

### Categories

| Prefix | Definition | Examples |
|---|---|---|
| `role-` | A role the agent plays when working a specific ticket (intake, BA, developer, QA, architect, security officer, product manager). Typically supports both `lane` (event-driven on ticket labels) and `request` (ad-hoc "run BA on this ticket"). | `role-ba`, `role-intake`, `role-developer` |
| `flow-` | An SDLC procedure that runs end-to-end and produces an artifact (PR, comment, tracker mutation, release note). Usually event-triggered or schedule-triggered. Some also make sense as one-shot requests (`flow-sprint-plan`, `flow-incident-postmortem`). | `flow-pr-self-review`, `flow-daily-retro`, `flow-release-notes` |
| `scan-` | A periodic audit that reports findings (tech debt, security deps, docs freshness, API contract breakage, accessibility). Always supports both modes — scheduled weekly *and* on-demand. | `scan-tech-debt`, `scan-security-deps`, `scan-api-contract` |
| `op-` | Automation that keeps Ship itself healthy (self-heal, retry sweep, stale issue sweep, knowledge refresh). Almost always `lane`-only (cron-driven). | `op-workflow-self-heal`, `op-retry-sweep`, `op-stale-issue-sweep` |
| `onboard-` | One-shot bootstrap procedures run during adoption (seed knowledge, harvest docs from repo, import backlog). Always `request`-only. | `onboard-adopt`, `onboard-seed-knowledge` |
| `common-` | Non-executable fragments included by other patterns (shared rules, prompt preambles, CI glue). `modes: []` and `group: common` so Library/Requests UIs can filter them out; other patterns pull their body via `spec.include: [common-base, ...]`. | `common-base`, `common-kickoff` |

### Rename map

All 21 current patterns rename in a single commit; no aliasing layer.

| Current id | New id | Category | Modes |
|---|---|---|---|
| `cloud-base` | `common-base` | common | `[]` |
| `kickoff` | `common-kickoff` | common | `[]` |
| `cloud-intake` | `role-intake` | role | `[lane, request]` |
| `cloud-clarification` | `role-clarification` | role | `[lane, request]` |
| `cloud-ba` | `role-ba` | role | `[lane, request]` |
| `cloud-developer` | `role-developer` | role | `[lane, request]` |
| `cloud-qa-architect` | `role-qa-architect` | role | `[lane, request]` |
| `cloud-tech-architect` | `role-tech-architect` | role | `[lane, request]` |
| `cloud-security-officer` | `role-security-officer` | role | `[lane, request]` |
| `catalog-a5-pr-self-review` | `flow-pr-self-review` | flow | `[lane]` |
| `catalog-a6-check-failure-recovery` | `flow-check-failure-recovery` | flow | `[lane]` |
| `catalog-a7-preview-validation` | `flow-preview-validation` | flow | `[lane]` |
| `catalog-a8-preview-failure-recovery` | `flow-preview-failure-recovery` | flow | `[lane]` |
| `catalog-a9-qa` | `flow-qa-acceptance` | flow | `[lane, request]` |
| `catalog-a10-human-handoff` | `flow-human-handoff` | flow | `[lane]` |
| `catalog-a11-retry-sweep` | `op-retry-sweep` | op | `[lane]` |
| `catalog-a12-learning` | `flow-learning-capture` | flow | `[lane, request]` |
| `catalog-a13-daily-retro` | `flow-daily-retro` | flow | `[lane, request]` |
| `cloud-workflow-self-heal` | `op-workflow-self-heal` | op | `[lane]` |
| `adopt-ship-generic` | `onboard-adopt` | onboard | `[request]` |
| `seed-knowledge-starters` | `onboard-seed-knowledge` | onboard | `[request]` |

## Metadata schema

Extends the `spec:` block in `ARTIFACT.md` frontmatter. Required for
executable patterns (categories `role`, `flow`, `scan`, `op`,
`onboard`). `common-*` patterns declare `modes: []` (implicit from
`group: common`) and usually omit `default_trigger` / `inputs` /
`lane_workflow`; they're only referenced via `spec.include`.

```yaml
artifact_kind: pattern
id: role-ba
name: BA / Specification
version: 1.0.0
# … existing fields …
spec:
  install_target: prompts/role/ba.md
  category: role                    # required; one of role|flow|scan|op|onboard|common
  modes: [lane, request]            # required; non-empty unless category=common
  include: [common-base]             # optional; bodies of these patterns get prepended at render
  default_trigger:                  # required when modes contains "lane"
    kind: event                     # event | schedule
    event: issues.labeled           # when kind=event
    pattern: "**"                   # event filter (branches, paths, labels)
    idempotency_key: "{{issue}}"    # when lane must dedupe
    # or for schedule kind:
    # kind: schedule
    # cron: "0 9 * * 1-5"
  # lane_workflow is OPTIONAL. The resolver picks a starter YAML by
  # category (see below); setting this key overrides the default.
  # lane_workflow: scheduled-sdlc-lane
  inputs:                            # required when modes contains "request"
    - name: ticket_url
      type: url
      required: true
      hint: "Ticket URL or ID"
    - name: depth
      type: enum
      values: [quick, thorough]
      default: thorough
  enabled_on_install:                # drives seed bundle per preset
    default: false
    presets:
      web-app: true
      api-backend: true
      monorepo: true
  knowledge_topics: [code-style, architecture]
```

### Lane workflow resolution

Each pattern installs into `.github/workflows/ship-<lane-id>.yml` from
one of four starter YAMLs shipped inside
`backend/app/services/starter_workflows.py`. The resolver picks a
default from `category` + `default_trigger`, and any explicit
`spec.lane_workflow` in the pattern's frontmatter overrides it:

| Category / trigger | Default starter | Rationale |
|---|---|---|
| `default_trigger.event == "pull_request"` (any category) | `pr-and-ci-gate` | Needs PR-comment permissions + PR-scoped context injection. |
| `category == "scan"` | `parallel-audit-lanes` | Fans out audits across a matrix; uses a different reporting path. |
| `category == "op"` + id starts with `op-workflow-` | `pipeline-self-heal` | Special `actions: write` permissions to rewrite CI files. |
| Everything else (`role`, `flow`, most `op`, `onboard`, `scan` when opted-in) | `scheduled-sdlc-lane` | Universal agent-run path; works for schedule *and* non-PR events. |

At lane-edit time the console library UI surfaces the resolved workflow
with an "Advanced → Override" control so operators can switch if the
default doesn't fit. The override lands in `.ship/config.yml` as a
per-lane `workflow: <id>` key and wins over frontmatter + resolver.

### Include resolution

`spec.include: [<pattern_id>, ...]` is walked once at
`shipctl run` render time. The CLI loads each included pattern's body,
prepends them in order, then the host pattern's body. Cycles raise; a
missing include fails loudly (we don't silently drop boilerplate the
pattern needed). Includes themselves may include other `common-*`
patterns — depth 2 is the max we enforce to keep rendering explainable.

### Input types

For the Requests form generator:

- `text` — single line string
- `textarea` — multi-line string
- `url` — URL with client-side validation
- `enum` — dropdown with `values: [...]` and optional `default`
- `bool` — checkbox
- `ref` — reference to another artifact (resolved in form via autocomplete)

## Schema v2.1 — multi-pattern lanes

RFC-0006 fixed one pattern per lane (`lanes.<id>.pattern: <pattern-id>`).
RFC-0008 promotes the general case — a lane is a **bundle of patterns
that share a trigger** — because the retired `DefaultPipelineSpec`
already treated `tech_debt` as three parallel role runs, and Phase-1
expansion adds more bundles (e.g. a "release hardening" lane running
`scan-security-deps` + `scan-api-contract`).

Canonical shape (v2.1):

```yaml
lanes:
  tech_debt_audit:
    schedule: "0 6 * * 1"
    patterns:
      - role-tech-architect
      - role-qa-architect
      - role-security-officer
```

Compatibility rules:

- `pattern: <id>` (scalar) is accepted as a **back-compat alias** for
  `patterns: [<id>]`. Single-pattern lanes keep the scalar shape on
  write so existing configs see zero diff.
- Exactly one of `pattern` / `patterns` is required per lane; sending
  both is rejected (`invalid_pattern_shape`).
- The emitter produces an inline YAML flow list (`patterns: [a, b,
  c]`) for multi-pattern lanes to keep diffs readable.

Runtime semantics:

- `shipctl run <lane>` currently rejects multi-pattern lanes with a
  usage error pointing at `parallel-audit-lanes.yml` (C3.2, next).
- `shipctl sync` / `shipctl lanes list` walk the full patterns list;
  the lockfile records provenance for every member pattern.
- The backend lanes-sync writes the full list into
  `Lane.config_blob.patterns` and keeps `Lane.pattern` = first element
  for existing single-pattern consumers until C3.4 renames `kind`.

Helpers (single source of truth):

- `cli/lib/config/schema.mjs` exports `lanePatterns(lane): string[]`
  (canonical list) and `lanePrimaryPattern(lane): string | null`.
- `backend/app/services/lanes_sync.py` does the same normalisation on
  parse.

## Retire DefaultPipelineSpec

`backend/app/services/default_pipelines.py` is replaced by
`backend/app/services/lane_recipes.py` as the source of truth for
baked-in lane wiring. The new module:

1. Walks `list_patterns()`, filters to patterns whose `modes` contains
   `lane` *and* which declare a stable `spec.lane_id`, and groups them
   by `lane_id` into :class:`LaneRecipe` records. Multiple patterns
   sharing a `lane_id` merge into a multi-pattern recipe.
2. Folds in two **non-pattern specials** (`code_map` — resolver-only,
   `tech_debt` — schedule-only placeholder until C5 lands the `scan-*`
   family). These specials keep their preset gating inline in the
   module; everything else derives from pattern
   `enabled_on_install.presets`.
3. `preset_bundle_files` / `/v1/catalog/lanes` / `seed_default_pipelines`
   all iterate `list_lane_recipes()` and gate on
   `resolve_enabled_lane_ids(preset)`.

Patterns that back a stable seeded lane declare three new optional
`spec` fields so the pattern id stays the authoritative *content*
identifier while the `lane_id` stays the authoritative *runtime*
identifier — materialised as `Pipeline.lane_id` in the DB since
C3.4:

```yaml
spec:
  modes: [lane]
  lane_id: pr_review            # stable slug shared with .ship/config.yml
  lane_name: "PR review"        # Library card title
  lane_summary: >-
    Reviews every pull request …   # Library card tagline
```

`DEFAULT_PIPELINES`, `PRESET_ENABLED_KINDS`, `DefaultPipelineSpec`,
`resolve_enabled_kinds` — all gone. `seed_default_pipelines` and
`KNOWN_PRESETS` keep their old signatures and just move to
`lane_recipes`. The catalog router's `_LANE_SUMMARIES` table is
deleted — summaries come from each pattern's `spec.lane_summary` (for
pattern-backed recipes) or inline from `_EXTRA_RECIPES` (for the two
specials).

The `lane_id` values stay byte-identical across C3.3 → C3.4
(`pr_review`, `daily_standup`, `tech_debt`, `self_heal`,
`code_map`); the C3.4 migration only swaps the column name so the
DB schema stops conflating "kind of pipeline" with "which lane
recipe does this row belong to". The table below shows how the
stable lane_ids map to the pattern(s) that back them today:

| Lane id | Pattern(s) | Notes |
|---|---|---|
| `pr_review` | `flow-pr-self-review` | Single pattern |
| `daily_standup` | `flow-daily-retro` | Single pattern |
| `self_heal` | `op-workflow-self-heal` | Single pattern |
| `tech_debt` | *(none yet)* | Picks up `scan-tech-debt` / `scan-security-deps` / `scan-architecture` with Expansion — C5 |
| `code_map` | *(none — resolver-only)* | Never lands in `.ship/config.yml` |

## Expansion — Phase 1 (10 new patterns)

All new patterns ship with the full metadata schema populated.

### Scanners (4)

| Id | Summary | Default trigger | Modes | Inputs (request form) |
|---|---|---|---|---|
| `scan-tech-debt` | Walks the repo for high-complexity files, duplication, TODO/FIXME clusters; files the top findings as tracker tickets. | schedule `0 6 * * 1` (weekly Mon 06:00 UTC) | `[lane, request]` | `scope: enum[full, last-sprint]` |
| `scan-security-deps` | Runs `npm audit` / `pip-audit` / `cargo audit` / `snyk` (whichever are configured), summarises critical/high findings, files one consolidated issue. | schedule `0 7 * * *` (daily 07:00 UTC) | `[lane, request]` | `severity: enum[critical, high, medium]` |
| `scan-docs-freshness` | Compares `documentation/` against code signatures and commit timestamps; files a tracker ticket per stale doc cluster. | schedule `0 8 * * 1` (weekly Mon 08:00 UTC) | `[lane, request]` | `doc_root: text (default: documentation)` |
| `scan-api-contract` | Compares current OpenAPI/GraphQL schema against the previous release; flags breaking changes on every PR that touches schema files, weekly summary for unreleased drift. | event `pull_request` with `paths: ['openapi.yaml', 'schema.graphql', '**/schema.py']` | `[lane, request]` | `base_ref: text (default: main)` |

### Flows (4)

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `flow-release-notes` | On release tag (or workspace request), synthesises changelog from merged PRs + closed tickets, opens a PR updating `CHANGELOG.md`. | event `push` on `refs/tags/v*` | `[lane, request]` | `from_ref: text`, `to_ref: text (default: HEAD)` |
| `flow-dependency-update` | Bumps one dependency at a time, runs tests in-branch, opens a PR with evidence. Cron-triggered weekly sweep. | schedule `0 5 * * 2` (weekly Tue 05:00 UTC) | `[lane, request]` | `ecosystem: enum[npm, pip, cargo, go]`, `package: text` |
| `flow-incident-postmortem` | One-shot: reads tracker issue tagged `incident`, reconstructs timeline from PRs/comments/runs, drafts RCA + action items. | `—` (request-only flow) | `[request]` | `incident_url: url (required)` |
| `flow-sprint-plan` | One-shot: reads backlog + team velocity, proposes sprint content by size. | `—` (request-only flow) | `[request]` | `sprint_length_days: enum[5, 10, 14]`, `capacity_points: text` |

### Ops (1)

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `op-stale-issue-sweep` | Weekly: tickets without activity for 30+ days get a nudge comment; 60+ days → proposed closure. | schedule `0 3 * * 3` (weekly Wed 03:00 UTC) | `[lane]` | — |

### Roles (1)

| Id | Summary | Default trigger | Modes | Inputs |
|---|---|---|---|---|
| `role-product-manager` | Triages fresh tickets: assigns size, priority label, and routes to the right role (`ready:ba` / `ready:developer` / `needs-clarification`). | event `issues.opened,reopened` | `[lane, request]` | `issue_url: url (required)` |

## Phase 2 (deferred, not in this RFC's implementation scope)

Tracking here so follow-up RFCs or tickets can reference it:

- Scanners: `scan-accessibility`, `scan-performance`, `scan-license`,
  `scan-dead-code`, `scan-test-coverage`.
- Flows: `flow-migration-guide`, `flow-test-gen`, `flow-pair-program`.
- Roles: `role-designer`, `role-devops`, `role-sre`.
- Ops: `op-knowledge-refresh`, `op-cost-audit`, `op-lane-health-report`.
- Onboarding: `onboard-harvest-docs`, `onboard-import-backlog`,
  `onboard-detect-conventions`.
- Tools: `azure-devops`, `jira`, `gitlab-ci`, `circleci`, `sonarqube`,
  `sentry`, `datadog`.

## UI impact

- `/lanes?tab=library` filters `list_patterns()` to `modes.lane` and
  groups cards by `category`. The schedule wizard's default values come
  from `pattern.default_trigger`.
- `/requests` (Phase 4) is rewritten: catalog grid of patterns with
  `modes.request`, clicking a card opens a dynamic form built from
  `pattern.inputs`, and dispatch sends `{pattern_id, inputs}` to
  `POST /requests`.
- The dashboard "Pipelines" section continues to exist but its rows
  come from `list_patterns()` joined with `Pipeline` DB state.

## Migration (clean break)

One PR per phase, merged sequentially:

1. **Phase 0 — schema contract.** Parse `category` / `modes` /
   `default_trigger` / `inputs` / `enabled_on_install` in
   `catalog.py`. Surface on `/v1/catalog/patterns` and add TS types.
   All 21 patterns still use legacy ids — this phase is purely
   additive for metadata.
2. **Phase 1 — rename.** Rename all 21 pattern directories + ids +
   references (collections, docs, tests, CLI templates, UI, starter
   workflows). Populate the new metadata fields per the rename table.
   Restamp SHAs. No backward-compat aliases; one breaking PR.
3. **Phase 2 — retire DefaultPipelineSpec.** Sub-steps C3.1–C3.4:
   - **C3.1.** Extend v2 schema (CLI + backend) to accept
     `lanes.<id>.patterns: [ids]`; `pattern: <id>` stays as alias.
     Emitter prefers `patterns: [...]`. Single-pattern lanes keep
     the scalar shape. *(landed with this RFC)*
   - **C3.2.** `run-agent.yml` is rewritten as `plan → run(matrix) →
     aggregate` so multi-pattern lanes dispatch one job per pattern
     (default `fanout: matrix`) or a single job that iterates the
     patterns internally (`sequential` / `concurrent`). The aggregate
     job collapses per-pattern outcomes into a single callback to
     Ship, so the backend still sees one `pipeline_run` per lane.
     The Library card editor exposes the three fan-out modes as a
     picker whenever the lane declares ≥2 patterns; single-pattern
     lanes never surface the picker and never emit a `fanout` key.
   - **C3.3.** Seed bundle + lane catalog endpoint + `Pipeline`
     seeding all pivot to walking patterns with `modes.lane` and
     `enabled_on_install.presets`. `PRESET_ENABLED_KINDS` is gone
     and `default_pipelines.py` is replaced by
     `backend/app/services/lane_recipes.py`, which folds catalog
     patterns and two non-pattern specials (`code_map` resolver-only,
     `tech_debt` multi-pattern placeholder) into a single ordered
     `LaneRecipe` list. Patterns that back a stable seeded lane
     declare `spec.lane_id` (e.g. `pr_review`), `spec.lane_name`
     and `spec.lane_summary` so the Library card / dashboard keep a
     human-friendly label decoupled from the pattern id.
     *(landed with this RFC)*
   - **C3.4.** Rename `Pipeline.kind` → `Pipeline.lane_id` +
     `uq_pipelines_workspace_kind` → `uq_pipelines_workspace_lane_id`
     (Alembic `0022_pipelines_rename_kind`). The column type and
     values are unchanged — this is a pure rename so the seeded
     lanes and the config-driven `Lane.lane_id` talk the same
     vocabulary. `_workflow_file_for_kind` / `_kind_to_workflow_id`
     are renamed to `_workflow_file_for_lane_id` /
     `_lane_id_to_workflow_id`; `KNOWLEDGE_PIPELINE_KINDS` →
     `KNOWLEDGE_PIPELINE_LANE_IDS`. The `PipelineOut.kind` JSON
     field and AuditLog `payload.kind` key stay put so Console and
     CLI clients don't need a round-trip change. *(landed with this
     RFC)*
4. **Phase 3 — Requests catalog UI (C4).** `/requests` rewritten to
   show the catalog grid + dynamic form. Backend accepts
   `{pattern_id, inputs}` and maps that onto the existing
   `adhoc-agent-run.yml` workflow dispatch payload.
   - New `/v1/catalog/patterns` endpoint returns `CatalogEntryOut` for
     every non-`common-*` pattern and supports `?mode=request|lane`
     for the two picker surfaces. Legacy pre-RFC-0008 patterns (no
     `category` / `modes`) are still returned so the Library/Requests
     grids keep rendering during the transition.
   - `AgentRequest` grows two columns: `pattern_id` (FK-shaped text
     slug, nullable) and `inputs` (JSONB, default `{}`) — Alembic
     `0023_agent_requests_pattern`. Pre-existing ad-hoc rows get
     `NULL` / `{}` and keep their free-form `agent_slug`+`prompt`
     semantics.
   - `POST /v1/workspaces/{ws}/repos/{id}/requests` accepts both
     shapes: pattern-backed (`{pattern_id, inputs}`) is preferred and
     validated against `pattern.spec.inputs` (required fields,
     `type: enum` value-set, type coercion); legacy
     (`{agent_slug, prompt, context_ref}`) still round-trips so older
     clients don't break. `pattern_id` + `pattern_inputs_json` are
     forwarded as `workflow_dispatch` inputs so `adhoc-agent-run.yml`
     can render the cataloged template via `shipctl run
     --pattern <id>`; empty when the caller uses the legacy shape.
   - Console `/requests` page is now a catalog grid grouped by
     `category` with one-card-at-a-time expansion. Each card renders
     a form built from `pattern.inputs` (text / url / enum /
     multiline widgets, required-field enforcement, sensible
     defaults). A separate "Ad-hoc prompt" card keeps the free-form
     dispatch path alive. Recent requests show the pattern id +
     inputs preview when present; otherwise the raw prompt. *(landed
     with this RFC)*
5. **Phase 4 — Expansion pack (C5).** 10 new Phase-1 patterns land
   as ARTIFACT.md files under `artifacts/patterns/<id>/`. They
   immediately show up in `/lanes?tab=library` (for `modes.lane`
   entries) and `/requests` (for `modes.request` entries) because
   both surfaces now fan out from `list_patterns()` — no starter
   workflow YAML is required until a user installs the pattern
   onto a repo. Every new pattern ships with the full RFC-0008
   metadata (`category`, `modes`, `default_trigger`, `inputs`,
   `enabled_on_install`) populated so the grid shows the right
   badges, the Requests form can render the dynamic inputs, and
   presets that opt in wire the lane on seed.
   *(landed with this RFC)*

Each phase is independently mergeable; each bumps `BUNDLE_VERSION`
where the seed payload changes.

## Open questions

None — all five design axes were answered during the RFC
preparation session (category-prefix naming, clean break, mandatory
modes, full expansion, full retire of `DefaultPipelineSpec`).
