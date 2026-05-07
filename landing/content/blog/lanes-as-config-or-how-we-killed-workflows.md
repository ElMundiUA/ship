---
title: Lanes as config — or how we killed the workflow artifact
slug: lanes-as-config-or-how-we-killed-workflows
date: 2026-04-23
kicker: Architecture
description: A full RFC, ten commits, one repo — in a single day we retired a first-class artifact kind, introduced lanes-as-config, and made shipctl run the single entry-point for everything a repo schedules. An autopsy of RFC-0007.
reading_time: 11
author: Denys Kuzin
tags: [architecture, rfc, autopsy, lanes]
---

On Apr 21 we retired a kind.

`artifact_kind=workflow` was a public part of the Ship catalog in the morning and a deleted folder by the evening. Ten commits moved it — a draft RFC, a schema, a migrator, a runner, a lockfile, a reusable workflow, the delete itself, a backend projection, a metrics bag, a Console page, an operator doc. Twelve hours end-to-end.

This is the autopsy of RFC-0007. It is about what a kind was carrying that did not belong to it, how we got it out, and why the last commit — the one where the old kind vanishes from the catalog — mattered more than the first nine.

## What was wrong with workflows-as-artifacts

An artifact in Ship is a pattern, a collection, a tool, or — until yesterday — a workflow. A workflow artifact was a `.github/workflows/<name>.yml` template checked into the Ship catalog, versioned like everything else, rendered into the customer's repo as `.github/workflows/ship-<name>.yml`. Each cadence had one. PR self-review, daily standup, tech-debt audit, self-heal — each was a row in the catalog with a body, a version, and a URL.

On paper the shape was tidy. The body of a workflow was a real file. The catalog carried a real version. The customer's repo got a real wrapper. It is how the rest of Ship works. We reached for the same shape, and for months we mostly got away with it.

In practice the kind was carrying two jobs it couldn't keep separate. The *what* — the methodology body, the prompt, the behaviour the agent enacts — lived in the paired pattern artifact. The *how* — the cron, the event, the permissions block, the runner label — lived in the workflow artifact. To change the *how* of one lane for one customer, you had to fork the workflow. To enable "PR self-review on this repo but not that one" you edited the generated `.github/workflows/ship-pr-review.yml` in their repo, and also the template in ours, and hoped nobody else had the first version.

The four workflow artifacts we shipped — `pr-and-ci-gate`, `scheduled-sdlc-lane`, `parallel-audit-lanes`, `pipeline-self-heal` — were clones of each other with different `on:` blocks. Every body ran the same three steps: `shipctl kickoff`, an agent invocation, `shipctl callback`. The only real difference was the trigger and the lane id. We had shipped a templating system where the template barely varied and the parameters were buried inside the template.

> We were maintaining a separate artifact kind to carry two variables: `on:` and the lane name.

Upgrades made this worse. Fleet-wide changes — fixing the way we dispatched `ship_run_id`, tightening permissions, adding a failure-fallback callback for runner flakes — meant re-merging small edits into N customer repos, each of which had accepted our YAML months ago and then not thought about it. Every customer's workflow file was a slightly different commit. There was no single place to bump a version of "how Ship lanes run".

Renaming a cron was two commits. Adding permissions was two commits. Raising a timeout was two commits — one in the catalog template, one in each generated wrapper. None of these commits were interesting. All of them were ours.

## The RFC: lanes-as-config

`documentation/protocol/rfc-0007-lanes-and-run-agent.md` is the design for what replaced all of that. Three moves, one shape.

A **lane** becomes a first-class entry in `.ship/config.yml` under a new top-level `lanes:` map. Each lane has a `kind` (`once` | `event` | `schedule`), a `pattern` reference, a trigger (`on:` for events, `cron:` for schedules, `idempotency:` for once), and an optional `permissions` / `runner` / `timeout_minutes` block. One paragraph of YAML per cadence. The v2 schema ships with the RFC — the `lanes` top-level key is only valid when `version: 2`, and v2 requires `shipctl_min >= "0.12.0"`.

A single reusable workflow in the Ship monorepo — `.github/workflows/run-agent.yml` — owns the GHA wiring. Checkout, setup-node, install shipctl at the pinned version, resolve the trigger, run the lane, stream the rendered prompt into an artifact downstream agents can consume, fallback-callback on hard failure. Customer repos render thin `.github/workflows/ship-<lane>.yml` wrappers that do nothing except call this reusable with the lane id.

A single CLI verb — `shipctl run --lane <id>` — is the entry point everything uses. Wrapper calls it. Operator calls it on the command line. The Ship dashboard calls it when it dispatches a run. Inside `run` we resolve config → pattern → idempotency → optional callback.

The pattern is the *what*. The lane is the *how*. The reusable workflow is the *glue*. The kind that used to sit in the middle — `artifact_kind=workflow` — has no job left.

Schema v1 is still parsed with a deprecation warning, so nobody's first command after pulling master is a hard error; `shipctl migrate` exists to close the gap. Anything that can be said in v1 can be said in v2. Most of what can be said in v2 cannot be said in v1 at all.

## Eight phases in one day

There were ten commits. Nine of them are the ones we care about, and they landed, in order, as receipts for the plan above.

**Phase 0 — the RFC and the validators.** `docs(protocol): RFC-0007 lanes-as-config + config schema v2 validators` put the spec on disk the same commit it put the enforcement on disk. No dangling prose, no schema-to-be-written-later. The v2 validator accepts the new shapes — lane kinds, cron strings, the `on:` enum, idempotency keys, the lane-id regex `[a-z0-9][a-z0-9_-]{0,63}` — and the v1 validator stays, emitting a deprecation warning with a pointer to `migrate`. `shipctl init` on a fresh repo now writes v2 by default. The write path normalises key ordering inside a lane while preserving the author's lane-id order, which is the kind of detail that sounds fussy until you diff two configs that a human wrote against two configs the CLI wrote.

**Phase 1 — `shipctl migrate`.** `feat(cli): shipctl migrate — v1 → v2 lanes-as-config upgrade`. A pure function takes a v1 config and returns a v2 config. It lifts `stack.agent.provider` into the new `agent.default.provider` slot without dropping the legacy field. It translates the four preset lane names — `pr_review`, `daily_standup`, `tech_debt`, `self_heal` — via a fixed mapping table that lives next to the function, not inside it. Lane ids the table doesn't recognise become stubs with a warning, because the alternative — silently inventing a `kind` — is how bad migrations happen. `--dry-run` prints the result, `--yes` writes a `.bak` before overwriting, `--json` exists for scripting. The command refuses to apply a result that would fail validation, which means the migrator cannot be the thing that breaks a repo's config.

**Phase 2 — `shipctl run` and the idempotency store.** `feat(cli): shipctl run + idempotency store + seed-knowledge-starters pattern`. The command reads the config, fits the trigger, fetches the pattern body, emits the prompt, and reports back. For `kind: once`, it reads a file-backed marker at `.ship/state/<key>.json` before doing anything and writes the marker on success. The marker schema is versioned (`version: 1`) so a future backend-store can migrate in place. For `event` and `schedule` the command recognises the kind and exits 0 with a deliberate "not yet wired" — the dispatch itself goes through the reusable workflow's agent step, which the wrapper invokes directly. The same commit migrated Ship's own `.ship/config.yml` to v2, which means the repo dogfooded the migrator before the migrator had external users. There is no better way to find out that your lane-id mapping table has the wrong `daily_standup` target than to run it against a config you wrote months ago.

**Phase 3 — the reusable workflow and `shipctl lanes install`.** `feat(cli,ci): Phase 3 — reusable run-agent.yml + shipctl lanes install`. Two artifacts, one commit. On the Ship side, `.github/workflows/run-agent.yml` is a `workflow_call`-callable reusable that does the boring bits: resolve the trigger, install `@elmundi/ship-cli` at the pinned version, run `shipctl run --lane <id>`, stream the rendered prompt into `.ship/run-output/prompt.md` and upload it as `ship-prompt-<lane>` for whoever consumes it, and — critically — on hard failure, call `shipctl callback --status fail` so a runner flake (setup-node, npm, a network blip before `shipctl run` gets a chance to report on its own) reconciles the same way as a clean failure. On the customer side, `shipctl lanes install` reads the `lanes:` block and renders one thin wrapper per lane. The `on:` map is derived from the lane's kind: `once` gets `workflow_dispatch` only; `event` adds the declared `on:`; `schedule` adds `schedule: [{ cron }]`. Each wrapper carries a banner — `# ship-cli: lanes v1` — so re-running the command can safely replace its own files and refuses, without `--force`, to touch anything it didn't generate.

**Phase 4 — `shipctl sync --lock`.** `cli: phase 4 — shipctl sync --lock, offline via lockfile`. A JSON lockfile at `.ship/shipctl.lock.json` records the resolved version, `content_sha256`, and `cached_path` for every pattern the declared lanes depend on, plus any pattern the config pins explicitly. `shipctl run --offline` resolves patterns exclusively via the lockfile and refuses to run on a sha mismatch. Online runs verify against the lockfile when it is present and warn on drift without failing — because in a fleet, drift warnings are how you learn to re-lock. The lockfile is tolerant of a manifest 5xx so air-gapped customers can still produce a clean lock out of cache. It is always safe to commit: it records only content hashes and paths, no secrets.

**Phase 6 — the delete.** `feat(protocol)!: Phase 6 — retire artifact_kind=workflow`. The bang in the commit subject is on purpose. The `workflow` kind is gone. `artifacts/workflows/` is deleted. The public API endpoints `GET /workflows`, `/workflows/{id}`, `/workflows/{id}/versions`, and `/v1/catalog/workflows` are removed from the backend. `shipctl workflow[s]`, the `workflow/<id>` pin syntax, and the `workflow` feedback kind are removed from the CLI; pinning `workflow/<id>` in `.ship/config.yml` is now a hard validation error. The `ApiArtifactKind` union in the console narrows to `pattern | tool | collection`. The four starter YAMLs, which are still needed for Ship's Pipeline installation flow, moved into `backend/app/resources/starter_workflows/` behind a private module. They are never reachable through the public catalog again.

This is the commit the other nine were for.

**Phase 7A — the backend learns lanes.** `feat(backend): Phase 7A — Lane projection model + sync API + webhook`. `.ship/config.yml` is the source of truth, but the backend can't round-trip to GitHub every time the Console wants to list lanes for a workspace. Alembic 0018 adds a `lanes` table unique on `(repo_id, lane_id)`, plus a nullable `pipeline_runs.lane_id` FK for future "Trigger lane now". A new service — `services.lanes_sync` — parses the v2 `lanes:` block and upserts one row per declared lane, deleting rows that disappear from the YAML and keeping the raw block in `config_blob` for forward compat. Two webhooks keep the projection honest: the `push` webhook re-syncs when `.ship/config.yml` is in the diff (and drops rows when the file is deleted); the `workflow_run.completed` webhook pins `last_run_at` and `last_run_status` on the matching Lane row by `ship-<lane_id>.yml` path match.

**Phase 7B — metrics in the callback.** `feat(cli,ci): Phase 7B — callback metrics for lane/GH reconciliation`. `shipctl run` now attaches a structured `metrics` bag to every callback POST: `lane_id`, `pattern_id`, `pattern_sha256`, `gh_workflow_run_id`, `gh_html_url`, `gh_event`. The failure-fallback step inside the reusable workflow attaches the same breadcrumbs so a hard runner failure reconciles to the same Lane row as a clean one. This is the commit that replaces "grep the logs to figure out which lane that run was for" with a small object on the wire.

**Phase 7C+D+E — the UI, the docs, the smoke test.** `feat(console,docs,ci): Phase 7C+D+E — lanes UI, operator docs, smoke matrix`. A `/lanes` page in the Console that groups lanes by repo, renders the kind pill, deep-links to the pattern, and shows the reconciled last-run badge — with an admin-only "Sync now" button per repo. A lane detail page with metadata, sync status, the raw config block as JSON, and the twenty most recent runs from `PipelineRun.lane_id`. A new `documentation/lanes.md` written as the operator reference. And a GHA smoke matrix — `.github/workflows/lanes-smoke.yml` — that stages a temp repo with a real v2 config per kind on every PR and nightly, asserts the expected status JSON, and verifies that a `once` marker short-circuits on a second run. The smoke job uses `SHIP_REPO` to resolve patterns locally, so the runner never hits the methodology API; it catches the kind of drift a code-only test would sleep through.

{{chart: blog-lanes-phases | The eight commits that retired a kind. Each bar is a phase; the color is the layer it moved in — CLI, backend, or Console — and the bar labelled Phase 6 is the one where the public kind actually vanishes. }}

Ten commits. Nine phases. One kind.

## Idempotency is committed, not gitignored

The decision we flipped at least once during the day was where the idempotency marker lives.

A `kind: once` lane's marker at `.ship/state/<key>.json` says "this lane completed against this pattern sha, at this time, from this GitHub run id". First instinct: gitignore it. It is runtime state. It changes. Nobody wants a diff every time a lane runs.

We tried it. Then we remembered what a marker is *for*. It is for the case where a fresh clone of the repo runs the lane and should *not* run it again. A gitignored marker is a marker that exists on one machine and on no other. The second runner does the work the first runner just did, writes a marker only it can see, and the third runner does it again. Idempotency without persistence is retry-blocking for one process, not for one organisation.

So the marker goes into the repo. RFC-0007 §Idempotency says so explicitly. The default `.gitignore` block that `shipctl init` writes includes `.ship/cache/` and `.ship/state.json` (the runtime singleton), but **not** `.ship/state/` (the idempotency directory). The generated workflow wrapper commits the marker on success via a PR, or a scratch branch, depending on the flow — the same mechanism the knowledge-seed PR uses. A fresh clone from any machine sees `seed-knowledge-starters.v1.json` under `.ship/state/` and immediately knows the lane is done.

> A gitignored idempotency marker is an idempotency suggestion.

We did not get this right on the first draft. An early version of the `.gitignore` block shipped with `.ship/state/` in it, because the intuition was "mutable local state, keep it out of git". We deleted that line the same day we wrote the idempotency store. The dogfood commit on Phase 2 — `.ship/state/seed-knowledge-starters.v1.json` committed alongside the freshly migrated config — is the receipt for that flip. It is one of the smallest files in the repo and one of the more load-bearing.

## Exit-0 no-op is not sad

The other small decision worth writing down: a marker hit is not a failure.

`shipctl run --lane <id>` on a lane whose marker already matches the current pattern sha writes `status: noop` via the callback and exits 0. The agent is not invoked. The pattern body is not re-fetched. No state changes. The callback carries enough metrics that the backend can tell the Lane row "last run was a no-op" — distinct from "last run was a success" and from "last run was a failure" — but as far as the ambient CI is concerned, the run is a green check.

This matters because GitHub Actions reruns are the happy path for `kind: once` lanes. Someone opens the Actions tab, clicks "Re-run all jobs" on the seed-knowledge workflow because they are testing something unrelated, and expects the run to be clean. If `shipctl run` failed on a marker hit — even with a precise error message — every rerun would produce a red X, and the operator would have to learn, one support ticket at a time, the difference between "this already happened" and "this is broken".

A no-op exit is how you tell the ambient CI that the system is working as designed. A hard failure is how you tell it that it isn't. We kept those two signals separate. The cost of that decision is a `status: noop` branch in the callback handler; the benefit is that the Actions tab doesn't lie. The point of idempotency is that the thing doesn't happen twice, not that the rerun fails.

## What the delete bought

Before Phase 6 the Ship catalog had 21 lane-shaped artifacts — four `workflow` kinds plus seventeen pattern and adapter items whose bodies were carrying trigger metadata because the workflow kind hadn't been honest enough to take it. After Phase 6: zero lane-shaped artifacts, N lane *entries* in each customer's `.ship/config.yml`, and four starter YAMLs living in `backend/app/resources/starter_workflows/` as private glue for the Pipeline installation flow.

The delete commit is short. Most of its diff is minus signs: the `/workflows` routes, the `shipctl workflow` verb, the `ApiArtifactKind` union entry, the `artifacts/workflows/` folder, the pin syntax, the feedback kind, the internal `kind → plural` map row that used to carry `"workflow": "workflows"`. Deleting a public kind is a breaking change and we shipped it as one — the bang in `feat(protocol)!` is the commit-message convention for "your pin syntax stops working today".

We shipped it as a single breaking change because we had no external callers. Same reasoning as the RFC-0005 cleanup commit two days earlier: the window for free shape changes closes the day someone else depends on you. We shipped Phase 6 while the window was still open. In two weeks, deleting a public route will be somebody's migration call; on Apr 21 it was a `git rm` and a test update.

The bookkeeping on deletion is larger than the diff looks. The Console lost three files — a page, a mock, a type map — and one route. The CLI's help output is shorter by a verb. The docs site is shorter by a page. The catalog page does not render a "Workflows" filter anymore. The landing nav does not either. Every one of those is a surface someone would have otherwise had to learn and unlearn.

Four consumers read lanes now. `shipctl run`, when the wrapper calls it. `shipctl lanes install`, when the operator regenerates the wrappers. The backend's `lanes_sync`, when the config changes on push. The Console's `/lanes` page, when someone clicks on it. All four read the same block of YAML. There is no second source.

> The workflow layer was a wrapper around the scheduling, not the methodology. Pulling it out of the catalog put the methodology back where it belongs — in patterns, one file, one shape.

Deleting a kind is rare. We do not intend to do it again soon. But this one had been worth it for a week before we finally did it, and the kind of commit Phase 6 is — a public route removal, an enum narrowing, a folder delete, a validation rule that turns a once-valid pin into a hard error — is the kind you cannot write six months from now without scheduling it.

On Apr 22 the Lanes hub in the Console got a calendar view and an Outlook-style schedule wizard. That is a different post. What this post is about is what the calendar view sits on top of: a config file, a reusable workflow, a single CLI verb, and one less public kind in the catalog than there was at breakfast.
