# Automations

An **Automation** is a [Play](./concepts.md#plays) assigned to a scope with a cadence — "run *PR review* on every pull request in this repo", "run *Technical audit* on the fleet every Monday at 06:00". Automations are how you make Ship *keep doing* something without anyone hitting a button.

This page is the operator reference for the Automations surface in the console: what you see on the page, how to create / edit / pause an Automation, what the Coverage tab is for, and how the operator UI compiles down to the `lanes:` block that has always lived in `.ship/config.yml`. For the field-by-field YAML schema — defaults, validation exit codes, every per-`kind` extra key — see [Configuration → `lanes`](./configuration.md#lanes). The normative spec is [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md) for the operator IA and [RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md) for the underlying execution model. When this page and `cli/lib/config/schema.mjs` disagree, the CLI wins.

## What an Automation is

> **Vocabulary box.** In the console you create and edit **Automations**. Under the hood, every Automation is a row in `lanes:` in the affected repo's `.ship/config.yml`, referencing a `pattern:` (the **Play**). Each execution lands in `pipeline_runs` and surfaces as a **Run**. The console renames `lane` to `Automation` to match how operators think; the YAML term is unchanged. See [Concepts](./concepts.md) for the full vocabulary.

Three things define an Automation:

1. **Play** — what to run (e.g. *PR review*, *Security deps scan*, *Technical audit*). Picked from the [Plays catalog](./concepts.md#plays).
2. **Scope** — where to run it. One of `repo` (one repo), `selected` (a chosen subset), or `fleet` (every activated repo in the workspace).
3. **Cadence** — when to run it. A trigger of `event` (webhook), `schedule` (cron), or `once` (idempotent bootstrap).

You assemble those three on the Automations page or via the *Automate* CTA on a Play card; the console writes the result back to the right `.ship/config.yml` files as a PR. You never edit Automations through the API directly — the YAML in your repo is canonical.

## The Automations console page

`/automations` has two tabs: **Active** and **Coverage**.

### Active tab

A list of every Automation visible to your current scope. The page accepts these query params:

| Param | Values | Effect |
|---|---|---|
| `scope` | `fleet` · `repo` · `all` | Filter to fleet-wide, single-repo, or everything. |
| `repo` | `<repo_id>` | Drill into one repo. The breadcrumb takes you back to the workspace view. |
| `play` | `<play_key>` | Show only Automations of one Play. |
| `status` | `enabled` · `disabled` | Hide paused Automations or only show paused ones. |

Each row shows the Play name, the scope (`this repo` / `selected: 4 repos` / `fleet`), the cadence (e.g. `Mon 09:00 UTC` or `on pull_request`), the next run time, and the last run outcome. Per-Automation actions: **Run now** (one-shot dispatch outside the cadence), **Edit**, **Pause / Resume**, **Delete**. Edits and deletes flow through a PR proposal against the affected repo's `.ship/config.yml` so the YAML stays the source of truth.

Clicking a row opens a detail drawer with:

- The full raw `lane` YAML the row compiled to.
- The sync source (`<ref_sha>:.ship/config.yml`).
- Up to 20 recent [Runs](./concepts.md#runs) for this Automation, with deeplinks into `/runs`.
- Any open Inbox items that escalated from those Runs.

### Coverage tab

Coverage answers a single question: **which Plays are running where, and where are the gaps?** It's a list (not a matrix — the matrix view is deferred to v2) sorted by uncovered count descending. Each row is one Play with a progress bar showing `N / M` activated repos that have it assigned. Critical Plays (`scan-security-deps`, `scan-license-deps`, `scan-pii-leakage`, `flow-pr-self-review`, `flow-incident-postmortem`, `flow-release-notes`, `flow-cert-compliance`) carry a red badge whenever coverage drops below 100%.

Drill-down on a row shows the **covered / uncovered split** — which repos already have this Play assigned and which don't — plus an **Apply to all uncovered** CTA. Hitting it opens the Automate wizard with the Play, scope (`selected: <uncovered repos>`), and a default cadence pre-filled; one PR per affected repo is opened with the new `lane` row.

If you're trying to figure out "what should we be running that we aren't yet", Coverage is the page to start on.

## Creating an Automation

Three entry points lead to the same wizard.

1. **From the Plays catalog.** Open `/plays`, find the Play, hit **Automate** on its card. Pick a scope and cadence. Submit.
2. **From the after-success banner on a Run.** When you trigger a Play with **Run now** and it succeeds, the result page shows a banner: *"→ Run this {play} every Monday automatically"*. One click opens the wizard with scope = the repo you ran it on and a sensible default cadence for the Play's category.
3. **From `shipctl init`.** A fresh install drops a starter `lanes:` block in `.ship/config.yml` covering the seven default Plays. You can edit those rows directly or hit the Automations page once the repo is activated; either path round-trips through the same YAML.

The wizard ends by opening a PR against each affected repo. The Automation appears on the page as soon as that PR merges to the default branch and the webhook lands.

## Editing and disabling

Editing happens through the same flow:

| Action | Console UI | Underlying effect |
|---|---|---|
| Edit cadence (cron, event filter, etc.) | Edit on the row → wizard pre-filled | Rewrites the matching `lane` row in `.ship/config.yml`; opens a PR. |
| Pause | **Pause** on the row | Edits the lane to `enabled: false` (or removes the trigger temporarily); the Console's Active tab keeps showing the row, marked *paused*. |
| Resume | **Resume** | Reverts the pause edit. |
| Delete | **Delete** on the row → confirm | Removes the `lane` entry from `.ship/config.yml`; the generated wrapper at `.github/workflows/ship-<lane>.yml` is dropped on the next `shipctl lanes install`. |
| Run outside the cadence | **Run now** on the row | One-shot dispatch (`adhoc-agent-run.yml`); doesn't change the cadence or write to YAML. |

For a pause that keeps the wrapper on disk (e.g. so you can re-enable it without a PR round-trip), use a GitHub Actions `if:` guard in the wrapper — but most teams find "delete + re-add" simpler and more auditable.

If you prefer the YAML-first path, the Navigator (and any human with PR access) can edit `.ship/config.yml` directly; the Console picks up the change on the next push to the default branch. Use the *Sync now* button on the page if a webhook missed the delivery.

## Triggers (cadence) at a glance

Every Automation declares exactly one trigger. The console wizard exposes them as **Schedule**, **Event**, and **One-off**; in YAML they appear as `kind: schedule` / `kind: event` / `kind: once`.

| Cadence | YAML `kind` | Extra fields | Fires when | Typical use |
|---|---|---|---|---|
| **One-off** | `once` | `idempotency: { key, store?, reset_on? }` | Dispatched manually or by the dashboard; guarded by a ledger key so duplicate runs no-op. | Seeding, one-off backfills, first-time installs |
| **Event** | `event` | `on: pull_request \| push \| workflow_run \| deployment_status` (+ optional `when: {…}`) | A matching webhook from the code host. | PR gates, code-review Plays, CI follow-ups |
| **Schedule** | `schedule` | `cron: "<5-field>"` (+ optional `cron_tz`) | UTC cron, 5 fields, GitHub Actions syntax. | Daily/weekly ceremonies, drift checks |

Automations are the *only* place triggers live. `shipctl` does not accept ad-hoc workflow YAMLs; the files under `.github/workflows/` named `ship-<lane_id>.yml` are generated by [`shipctl lanes install`](/cli#shipctl-lanes-install) and carry a `# ship-cli: lanes v1` banner. Hand-edits outside the banner survive re-runs; edits inside the banner will be overwritten.

## Relationship to `lanes:` in `.ship/config.yml`

Under the hood, every Automation is one row in `lanes:` in the affected repo's `.ship/config.yml`. The console renames `lanes` to **Automations** to make the operator surface clearer; the YAML schema is unchanged from [RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md). For the full schema — every per-`kind` extra key, defaults, validation messages — see [Configuration → `lanes`](./configuration.md#lanes).

A worked YAML example covering all three trigger kinds:

```yaml
version: 2
shipctl_min: "0.12.0"

lanes:
  pr_review:
    kind: event
    on: pull_request
    pattern: flow-pr-self-review

  daily_retro:
    kind: schedule
    cron: "0 9 * * 1-5"
    patterns:
      - flow-daily-retro

  seed_knowledge:
    kind: once
    pattern: onboard-seed-knowledge
    idempotency:
      key: seed-knowledge-v1
      store: file
      reset_on: version-change
```

Each entry compiles to one Automation in the console. Keys under `lanes:` are lane ids matching `/^[a-z0-9][a-z0-9_-]{0,63}$/` (ids starting with `ship_` are reserved). Each lane declares exactly one trigger (`kind`) and exactly one of `pattern:` / `patterns:`.

### Multiple patterns per Automation (composite Plays)

A composite Play maps to several patterns sharing one trigger — *Technical audit*, for example, runs a tech-architect, QA, and security review on the same schedule:

```yaml
lanes:
  tech_debt_audit:
    kind: schedule
    cron: "0 6 * * 1"
    patterns:
      - role-tech-architect
      - role-qa-architect
      - role-security-officer
```

Rules:

- `pattern: <id>` (scalar) and `patterns: [<id>, …]` (list) are mutually exclusive — declare exactly one.
- `patterns` must be a non-empty list of existing pattern ids.
- `shipctl run --lane <id>` dispatches every pattern the lane declares (a composite lane with `patterns: [a, b, c]` will fan out across all three using the lane's `fanout:` setting — `matrix` by default). To target one specific pattern inside a composite lane, pass `--pattern <id>`. See `cli/tests/run.test.mjs` for the full multi-pattern coverage.

## What `shipctl` does with a lane

Four commands deal with lanes — and therefore with Automations — directly:

- **`shipctl migrate`** — one-shot upgrade from v1 → v2. Backs up the current file to `.ship/config.yml.bak`, moves `stack.agent.provider` → `agent.default.provider`, and translates a legacy `lanes: [id, id, …]` list into the v2 lanes map using preset defaults. Safe to re-run: already-v2 configs exit `0` with `no changes`.
- **`shipctl lanes install`** — reads `.ship/config.yml`, renders one `.github/workflows/ship-<lane>.yml` wrapper per declared lane that calls `ElMundiUA/ship/.github/workflows/run-agent.yml@vN` (the `ref` defaults to `v<shipctl_min>`). Banner-guarded with `# ship-cli: lanes v1`. Refuses to overwrite a non-Ship file without `--force`. Idempotent. `shipctl lanes remove` only deletes files it owns.
- **`shipctl run --lane <id>`** — the single execution entry point. Today's scope: `kind: once` runs end-to-end (resolve the pattern, check the idempotency marker, emit the prompt on stdout, write the marker, POST the callback). `kind: event` and `kind: schedule` are parsed, validated, and reported as a `status: noop` — they rely on `shipctl lanes install`-generated wrappers that GitHub triggers, and Phase 3 of RFC-0007 wires the reusable workflow. `--offline` resolves patterns exclusively through `.ship/shipctl.lock.json`.
- **`shipctl sync --lock`** — materialises every lane's pattern into `.ship/cache/` and writes `.ship/shipctl.lock.json`. The lockfile is safe to commit and is the reproducibility anchor for `run --offline`.

If you're installing Ship for the first time, `shipctl init` drops a starter `lanes:` block for you and runs `shipctl lanes install` to generate the matching wrappers.

## What the backend does with an Automation

Two things:

1. **Projects it into a `lanes` table** that the Console reads. The table is a cache — the YAML file is the source of truth — so toggling an Automation on/off through the API is *not* supported. You edit the YAML (or use the wizard, which opens a PR for you) and push. The cache refreshes automatically when a push to the default branch touches `.ship/config.yml`, and you can force a re-pull with the *Sync now* button.
2. **Reconciles Runs.** When GitHub sends a `workflow_run.completed` event for `ship-<lane_id>.yml`, the backend pins `last_run_at` / `last_run_status` on the matching row. That's what powers the freshness badge in the console without polling GitHub per page load.

## Migrating from the old "workflow artifact" world

[RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md) removed `artifact_kind=workflow` from the public surface. What used to be a workflow artifact is now a `lane` declared in `.ship/config.yml` — and an **Automation** in the console:

| Before | After |
|---|---|
| `.github/workflows/ship-pr-and-ci-gate.yml` | `lanes.pr_review:` — `kind: event`, `on: pull_request`, `pattern: flow-pr-self-review` |
| `.github/workflows/ship-scheduled-sdlc-lane.yml` | `lanes.daily_retro:` — `kind: schedule`, `cron: "0 9 * * 1-5"`, `pattern: flow-daily-retro` |
| `.github/workflows/ship-onboard-seed-knowledge.yml` | `lanes.seed_knowledge:` — `kind: once`, `idempotency: { key: seed-knowledge-v1 }`, `pattern: onboard-seed-knowledge` |

The wrapper filenames survive as starter scaffolding (e.g. `scheduled-sdlc-lane.yml` is still the filename the starter renderer emits, even though `scheduled-sdlc-lane` is no longer a pattern id). `shipctl lanes install` regenerates them from the v2 lane block.

If your repo still ships a hand-written workflow that hasn't been migrated, the generator will refuse to overwrite it (no `ship-cli: lanes v1` banner); delete the file, pass `--force`, or migrate your config through `shipctl migrate`.

## Cookbook

### Add a new Automation

1. Edit `.ship/config.yml`:

    ```yaml
    lanes:
      friday_retro:
        kind: schedule
        cron: "0 15 * * FRI"
        pattern: flow-daily-retro
    ```

2. `shipctl lanes install` to write `.github/workflows/ship-friday_retro.yml`.
3. `shipctl sync --lock` to refresh `.ship/shipctl.lock.json` with the new pattern.
4. Commit `.ship/config.yml`, the generated wrapper, and the updated lockfile in the same PR. The Console picks it up on the next push to the default branch.

The console wizard does steps 1–4 for you; doing it by hand is the fallback for offline editing or scripted migrations.

### Pause an Automation temporarily

Comment out (or delete) the lane block and push. The backend cleans up the row on the next webhook tick; the generated wrapper will stop being rendered by `shipctl lanes install` on the next run. Use `shipctl lanes remove --only <id>` to drop the wrapper file (it only deletes files carrying the Ship banner). Or hit **Pause** on the row in the console — same end state, less ceremony.

### Trigger a Play manually outside the cadence

Use **Run now** on the Automation row, or on the underlying Play card on `/plays`. Either dispatches a one-shot `adhoc-agent-run.yml` and streams the run back into `/runs`. For a stable manual override that bypasses the dashboard entirely, use the native GitHub Actions **Run workflow** menu on any `ship-<lane_id>.yml` wrapper — wrappers expose `ship_run_id` and `ship_callback_url` inputs so the run reports back to the dashboard the same way a scheduled run would.

### Find Plays you should be running but aren't

Open the **Coverage** tab on `/automations`. Sort is uncovered-count descending; critical Plays in red. Drill into a row to see the covered / uncovered split, and use **Apply to all uncovered** to fan out one PR per affected repo.

### Keep the `lanes` table in sync

In normal operation you don't have to do anything — push-to-default keeps the cache fresh. Use **Sync now** on `/automations` when:

- you rewrote history and the webhook didn't fire;
- you're debugging a parse error and need the backend's take on the current YAML;
- you just flipped an Automation on/off and want the Console to reflect it without waiting for the next push.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/automations` shows no rows despite a `lanes:` block in `.ship/config.yml` | Either (a) `.ship/config.yml` wasn't pushed to the default branch yet, or (b) webhook delivery hasn't landed. | Push the commit; if the webhook is lost, click **Sync now** on the page. |
| "Missing `.ship/config.yml`" on sync | Repo's default branch has no `.ship/config.yml`. | Run `shipctl init` or point the `default_branch` at the branch that has it. |
| `shipctl run --lane X` exits 2 | `.ship/config.yml` is still v1. | Run `shipctl migrate`, review `.ship/config.yml.bak`, commit the new v2 file. |
| Automation row shows trigger `event` but the Run never fires | The wrapper file wasn't regenerated after the YAML change. | Re-run `shipctl lanes install`, commit the updated wrapper. |
| `shipctl lanes install` refuses to overwrite `.github/workflows/ship-<id>.yml` | The existing file lacks the `# ship-cli: lanes v1` banner; the generator assumes it's hand-authored and won't clobber it. | Delete the file or pass `--force` to replace it. |
| Automation row stays `running` forever | `workflow_run.completed` webhook delivery failed. | Re-send the delivery from the GitHub App settings → Advanced → Recent deliveries. |
| `shipctl sync --lock` reports unresolved entries | An Automation references a pattern id that isn't published on the configured channel. | Fix the pattern id (see [RFC-0008](./protocol/rfc-0008-catalog-reform.md) for the current naming), or switch to `channel: edge`. |

## See also

- [Concepts](./concepts.md) — the operator vocabulary, including Plays, Runs, and Inbox.
- [Configuration → `lanes`](./configuration.md#lanes) — the field-by-field YAML schema.
- [RFC-0010 — Plays, Automations, Runs, Inbox](./protocol/rfc-0010-plays-and-inbox.md) — the operator IA spec.
- [RFC-0007 — Lanes and `run-agent.yml`](./protocol/rfc-0007-lanes-and-run-agent.md) — the underlying execution model.
- [RFC-0008 — Catalog reform (pattern naming)](./protocol/rfc-0008-catalog-reform.md) — the catalog ids referenced from `pattern:` / `patterns:`.
- [CLI → `shipctl run`](/cli#shipctl-run) · [`shipctl lanes install`](/cli#shipctl-lanes-install) · [`shipctl migrate`](/cli#shipctl-migrate)
