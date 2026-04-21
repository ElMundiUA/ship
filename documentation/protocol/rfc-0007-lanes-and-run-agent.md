---
rfc: 0007
title: "Lanes-as-config and the single `shipctl run` entry-point"
status: Draft
created: 2026-04-21
supersedes_in_part: [rfc-0002]
---

# RFC-0007 — Lanes-as-config and the single `shipctl run` entry-point

## Abstract

Today Ship ships three overlapping layers for every automated cadence:

1. A `.github/workflows/*.yml` file in the customer repo that runs
   `shipctl kickoff` + agent invocation + `shipctl callback`.
2. A versioned **workflow artifact** in the Ship catalog
   (`artifacts/workflows/<id>/workflow.yml`) that serves as the template
   for (1).
3. A versioned **pattern artifact**
   (`artifacts/patterns/<id>/ARTIFACT.md`) that carries the actual prompt
   the agent consumes.

Layer (2) adds zero logic: it's a shell around (1) and (3). Each new
cadence multiplies three files. This RFC collapses the model:

- **`lanes`** become first-class entries in `.ship/config.yml` (schema
  v2). A lane declares its **kind** (`once` | `event` | `schedule`), its
  **pattern**, and the metadata needed to execute it.
- **`shipctl run --lane <id>`** becomes the single entry-point that
  customer CI pipelines (or operators) invoke. It resolves config →
  pattern → idempotency → agent invocation → callback.
- **Workflow artifacts are deprecated** (`artifact_kind=workflow`) —
  replaced at install time by `shipctl init` generating thin wrappers
  around a single reusable workflow published in the Ship repo.

## Motivation

See the implementation-plan thread that produced this RFC, but in short:

- Workflow artifacts currently clone each other (`pr-and-ci-gate`,
  `scheduled-sdlc-lane`, `parallel-audit-lanes`, `pipeline-self-heal`,
  `hosted-e2e-regression`). The only real difference is `on:` and the
  lane id — every body runs `kickoff → (agent) → callback`.
- Customers edit three files to adopt a new cadence. Upgrades require
  re-merging YAML that Ship shipped months ago with trivial drift.
- Non-GitHub schedulers (GitLab, Jenkins, self-hosted cron) are marked
  `planned` in README because porting workflow artifacts one-by-one is
  disproportionate to the value.

## Terms

- **Lane** — a Ship-managed cadence: a paragraph of config that binds a
  trigger (once, cron, event) to a pattern.
- **Pattern** — unchanged from RFC-0001 / RFC-0005: a versioned
  `ARTIFACT.md` carrying the agent prompt.
- **Kind** — `once` | `event` | `schedule`. Defines how Ship decides
  whether a lane should fire for the current invocation.
- **Idempotency marker** — small JSON file under `.ship/state/` (or
  backend-stored key) that records whether a `once` lane has already
  completed, and for which version of its pattern.

## Normative schema — `.ship/config.yml` v2

```yaml
version: 2
shipctl_min: "0.12.0"
preset: monorepo
repo: ElMundiUA/ship

api:
  base_url: "https://ship.elmundi.com"
  channel: stable

agent:
  default: { provider: cursor-cloud }
  overrides:
    pr_review: { provider: claude-code }

lanes:
  seed_knowledge_starters:
    kind: once
    pattern: seed-knowledge-starters
    idempotency:
      key: seed-knowledge-starters.v1
      store: file               # file | backend
      reset_on: version-change  # version-change | manual
  pr_review:
    kind: event
    on: pull_request
    pattern: catalog-a5-pr-self-review
    permissions:
      contents: read
      pull-requests: write
  daily_standup:
    kind: schedule
    cron: "0 9 * * 1-5"
    pattern: catalog-a13-daily-retro
```

### Field reference

| Field                                         | Required | Type    | Notes |
|-----------------------------------------------|----------|---------|-------|
| `version`                                     | yes      | int     | `2`. v1 configs validated separately; `shipctl migrate` converts.
| `shipctl_min`                                 | yes      | semver  | Minimum shipctl that understands v2. Enforced on read.
| `agent.default.provider`                      | no       | string  | Agent slug (`claude-code`, `cursor-cloud`, …).
| `agent.overrides.<lane>.provider`             | no       | string  | Per-lane override.
| `lanes.<id>`                                  | yes      | object  | Lane definition. `<id>` must match `[a-z0-9][a-z0-9_-]{0,63}`.
| `lanes.<id>.kind`                             | yes      | enum    | `once` \| `event` \| `schedule`.
| `lanes.<id>.pattern`                          | yes      | string  | Pattern artifact id. `min_shipctl` on the pattern is enforced.
| `lanes.<id>.pattern_version`                  | no       | semver  | Pin a specific pattern version.
| `lanes.<id>.permissions`                      | no       | map     | GitHub permissions block — propagated to generated wrappers.
| `lanes.<id>.runner`                           | no       | string  | GitHub runner label (default `ubuntu-latest`).
| `lanes.<id>.timeout_minutes`                  | no       | int     | Default 15.
| `lanes.<id>.concurrency`                      | no       | object  | `{ group: "...", cancel_in_progress: bool }`.
| `lanes.<id>.idempotency.key` (kind=once)      | yes      | string  | Durable id. Included in the marker path.
| `lanes.<id>.idempotency.store` (kind=once)    | no       | enum    | `file` (default, marker under `.ship/state/`) or `backend`.
| `lanes.<id>.idempotency.reset_on` (kind=once) | no       | enum    | `version-change` (default) or `manual`.
| `lanes.<id>.cron` (kind=schedule)             | yes      | string  | Standard 5-field cron. UTC unless `cron_tz` set.
| `lanes.<id>.cron_tz` (kind=schedule)          | no       | string  | IANA TZ. Default `UTC`.
| `lanes.<id>.on` (kind=event)                  | yes      | string  | `pull_request` \| `push` \| `workflow_run` \| `deployment_status`.
| `lanes.<id>.when` (kind=event)                | no       | map     | Event filter (e.g. `{ conclusion: failure }`).

### Lane-id reservations

IDs starting with `ship_` are reserved for Ship-owned lanes shipped via
presets. `shipctl init` treats them as managed and may rewrite them on
upgrade; customer-owned lanes keep any other naming.

## Normative contract — `shipctl run`

```
shipctl run --lane <id> [--dry-run] [--offline] [--trigger <event|schedule|manual|once>]
            [--ship-run-id <uuid>] [--ship-callback-url <url>] [--ship-run-token <jwt>]
            [--cwd <dir>] [--json]
```

Execution order:

1. **Resolve root.** Walk upward from `--cwd` for `.ship/config.yml`.
   Abort if not found.
2. **Load and validate** v2 config. If v1: exit 2 with a message
   pointing at `shipctl migrate`.
3. **Resolve lane** by id. Exit 1 on unknown lane.
4. **Fit trigger.** If `--trigger` is supplied, reject if it does not
   match the lane's `kind`. If absent, infer from env
   (`GITHUB_EVENT_NAME`, `SHIP_RUN_TRIGGER`). Unknown → default to
   `manual`, which is only valid for `kind=once` (anything else exits 0
   as a no-op with a warning).
5. **Check idempotency** for `kind=once`:
   - Read `.ship/state/<idempotency.key>.json`.
   - If present and `pattern_sha256` matches current pattern — exit 0
     with `status=noop`. Do **not** call the agent, do **not** call
     callback.
   - If present but `pattern_sha256` differs and `reset_on=version-change`
     → proceed and overwrite marker on success.
6. **Fetch pattern** via the same path `shipctl kickoff` uses. In
   `--offline` mode, read from `.ship/patterns/<id>.md` or the local
   monorepo tree (when running inside Ship).
7. **Emit prompt** (today's MVP: stdout + `X-Ship-Lane` hint on stderr).
   The agent invocation itself stays in the CI workflow; `shipctl run`
   does not fork a subprocess for the agent yet (that's Phase 1b).
8. **Record idempotency** (for `kind=once`, on success).
9. **Callback** (if `--ship-callback-url` or `SHIP_CALLBACK_URL`
   present). Uses the same code path as `shipctl callback`.

Exit codes:

- `0` — lane executed (or no-op / already-done).
- `1` — usage / config error.
- `2` — v1 config encountered (migration required).
- `3` — network / callback failure.
- `4` — idempotency write failure (markers must not silently disappear).
- `10` — auth (invalid or missing `SHIP_RUN_TOKEN` when callback URL set).

## Idempotency store

### `store: file` (default)

Marker path: `.ship/state/<idempotency.key>.json`, format:

```json
{
  "version": 1,
  "lane": "seed_knowledge_starters",
  "pattern_id": "seed-knowledge-starters",
  "pattern_sha256": "…",
  "pattern_version": "1.0.0",
  "completed_at": "2026-04-21T14:20:00Z",
  "by": { "run_id": "…", "host": "github-actions" }
}
```

Markers are expected to be committed. Generated workflow wrappers open a
PR (or commit to a scratch branch) containing the marker when the lane
succeeds — same mechanism Ship's knowledge-seed PR uses today.

### `store: backend`

Reserved for Phase 5. Marker lives server-side keyed by
`(workspace, repo, idempotency.key)`. Not implemented in the first cut.

## Deprecation — `artifact_kind=workflow`

1. All entries under `artifacts/workflows/` carry `deprecated: true` in
   front-matter, effective next release.
2. Backend continues to serve them but adds `X-Ship-Deprecated: true`.
3. `shipctl search|pattern|workflow` print a one-line warning when a
   hit is a deprecated workflow.
4. Two minor releases later, the `workflow` kind is removed from
   `KINDS` in `cli/lib/config/schema.mjs`.
5. During the window, the replacement path is:
   - a reusable workflow `ElMundiUA/ship/.github/workflows/run-agent.yml@vN`
     in this repo, and
   - thin wrappers generated by `shipctl lanes install` from `lanes.*` in
     `.ship/config.yml`.

## Reusable workflow contract

Published from this repo at `/.github/workflows/run-agent.yml`, callable
as `uses: ElMundiUA/ship/.github/workflows/run-agent.yml@vN`.

Inputs (workflow_call):

- `lane` (string, required) — matches a key in `.ship/config.yml` `lanes.*`.
- `trigger` (string, optional) — derived from `github.event_name` when
  empty: `workflow_dispatch` → `manual`, `schedule` → `schedule`,
  otherwise `event`. The resolved value is passed through to
  `shipctl run --trigger`.
- `shipctl_version` (string, default `latest`) — npm dist-tag / semver
  for `@elmundi/ship-cli`.
- `node_version` (string, default `20`).
- `dry_run`, `offline` (boolean).
- `ship_run_id`, `ship_callback_url` (string, passed through when
  provided by the dispatching caller).
- `upload_prompt` (boolean, default `true`) — uploads the rendered
  prompt as artifact `ship-prompt-<lane>` so downstream agent CLIs
  (or humans) can consume it.

Secrets (workflow_call):

- `SHIP_API_TOKEN` (optional) — methodology API auth.
- `SHIP_RUN_TOKEN` (optional) — the short-lived bearer issued by Ship
  when the dashboard dispatched this run.

Body: checkout → setup-node → install shipctl → resolve trigger →
`shipctl run --lane … --trigger …` with stdout streamed to
`.ship/run-output/prompt.md` and stderr to
`.ship/run-output/shipctl.log`. On failure, a fallback `shipctl
callback --status fail` step covers the case where the run failed
before `shipctl run` could report on its own (e.g. `setup-node`
flake). Caller-side wrappers supply `on:`, `permissions:`,
`concurrency:`, and the lane id — nothing else.

### Thin wrapper shape

`shipctl lanes install` reads the v2 config and renders one
`.github/workflows/ship-<lane_id>.yml` per declared lane, each of the
form:

```yaml
# ship-cli: lanes v1 — generated by `shipctl lanes install`.
name: Ship · pr_review
on:
  workflow_dispatch:
    inputs: { ship_run_id: ..., ship_callback_url: ... }
  pull_request: null
permissions:
  contents: read
  pull-requests: write
jobs:
  run:
    uses: ElMundiUA/ship/.github/workflows/run-agent.yml@v0.12.0
    with:
      lane: pr_review
      shipctl_version: latest
      ship_run_id: ${{ inputs.ship_run_id }}
      ship_callback_url: ${{ inputs.ship_callback_url }}
    secrets: inherit
```

The `on:` block is derived from `lane.kind`:

| `lane.kind` | emitted `on:` triggers                                             |
| ----------- | ------------------------------------------------------------------ |
| `once`      | `workflow_dispatch` only                                           |
| `event`     | `workflow_dispatch` + `<lane.on>` (e.g. `pull_request`)            |
| `schedule`  | `workflow_dispatch` + `schedule: [{ cron: <lane.cron> }]`          |

Wrappers are banner-guarded: `shipctl lanes install` refuses to
overwrite a ship-named file without the `ship-cli: lanes v1` banner
unless `--force` is passed; `shipctl lanes remove` only deletes files
it recognises as its own output.

Public availability: the reusable workflow must live in a public Ship
repo for non-enterprise customers. Enterprise customers can mirror the
same file into their own org and point `--owner/--repo` at the fork.
Inlining the reusable workflow is out of scope for Phase 3.

## Migration

`shipctl migrate` is a new command. Algorithm:

1. Read current config. If `version=2`, no-op.
2. For `version=1`:
   - Preserve `api`, `stack`, `artifacts.pins`, `telemetry`, `cache`
     verbatim.
   - Move `stack.agent.provider` → `agent.default.provider`.
   - Translate `lanes: [pr_review, daily_standup, tech_debt, self_heal]`
     (current list-of-strings shape) into the v2 lanes map using the
     preset's default mappings (documented in
     `documentation/docs/lanes/presets.md`).
   - Bump `version` to `2` and `shipctl_min` to the release introducing
     v2.
   - Write backup to `.ship/config.yml.bak`.

A missing mapping aborts the migration with an actionable message
instead of a silent drop.

## Open questions

1. **Reusable workflow availability for private orgs** — Phase 3 ships
   with `--owner`/`--repo` overrides on `shipctl lanes install`; a
   private fork of `ElMundiUA/ship` is therefore supported but inlining
   the reusable body into each wrapper is still deferred.
2. **Idempotency marker PR flow** — do workflows always auto-commit, or
   do we rely on `actions/upload-artifact` for CI runs where direct
   commit is undesirable? Default: auto-commit via a dedicated
   `shipctl run --autocommit` subpath; document the artifact fallback.
3. **Pattern versioning on idempotency reset** — `reset_on=version-change`
   considers the full pattern `content_sha256`. Should we offer a
   `reset_on=semver-major` variant? Out of scope for v1.
4. **Backend-store idempotency** — Phase 5 scope; needs an API route on
   `api.ship.elmundi.com` plus auth via workspace PAT.
5. **Legacy `lanes:` as array of strings in v1** — kept accepted by the
   v1 validator until deprecation window closes; conversion is
   performed by `shipctl migrate`.

## Compatibility with earlier RFCs

- RFC-0001 / RFC-0005 — no changes. Patterns stay the primary prompt
  carrier.
- RFC-0002 — this RFC supersedes section "lanes" (previously just a
  string array) and introduces `agent.default/overrides`. The rest of
  the v1 schema remains valid under `version: 1`.
- RFC-0004 — adapters unchanged. Lanes reference patterns, patterns
  reference adapter tools — this RFC does not change that chain.
- RFC-0006 — cloud platform foundations unchanged; lanes are a
  repository-local concept, orthogonal to workspace/org hierarchy.

## Implementation phasing

See [`PLAN.md`](../plan/lanes-implementation.md) (linked from
README under the development section, added in the same PR series):

- **Phase 0 (this RFC).**
- **Phase 1** — `shipctl run` with `kind=once` only.
- **Phase 2** — Schema v2 + `shipctl migrate`.
- **Phase 3** — Reusable `run-agent.yml` + `shipctl lanes install`
  (thin wrapper generator, idempotent, banner-guarded).
- **Phase 4** — Lock file + offline.
- **Phase 5** — Backend idempotency store.
- **Phase 6** — Deprecation close-out for `artifact_kind=workflow`.
- **Phase 7** — Console UI + docs.
- **Phase 8** — Non-GitHub scheduler adapters.
