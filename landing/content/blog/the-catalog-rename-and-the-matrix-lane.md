---
title: The catalog rename and the matrix lane
slug: the-catalog-rename-and-the-matrix-lane
date: 2026-04-25
kicker: Autopsy
description: 21 patterns renamed across 78 files. A new six-category scheme. Five duplicates deleted. Then a multi-pattern lane with three fan-out modes. RFC-0008, in the order it actually shipped, and why the matrix execution model fell out of the rename.
reading_time: 11
author: Denys Kuzin
tags: [catalog, rfc, autopsy, lanes]
---

On Apr 22 we reformed the Ship catalog.

Three commits, in order: we deleted five duplicate patterns, we renamed the remaining 21 onto a canonical six-category scheme with a proper metadata block, and then — because the rename made it legal — we taught a lane to hold more than one pattern and fan them out across a GitHub Actions matrix.

That is the sentence we want on the wall. The rename was not cosmetic. The rename was what let the execution model change.

This post is the autopsy of RFC-0008 Phases 0, 1, and the first half of Phase 2 (C3.1 and C3.2). We are going to walk the three commits in the order they landed and then name the principle, because the principle is the part we want to remember next time we are tempted to treat namespace cleanup as a chore.

## Two namespaces, no intent

Before Apr 22, the Ship catalog had parallel families of the same thing.

There was a `cloud-*` family — `cloud-intake`, `cloud-clarification`, `cloud-ba`, `cloud-developer`, `cloud-qa-architect`, `cloud-tech-architect`, `cloud-security-officer`. These were the SDLC roles an agent plays against a specific ticket. Fine.

There was also a `catalog-a*` family — `catalog-a1-intake`, `catalog-a2-clarification`, `catalog-a3-ba-spec`, `catalog-a4-developer`, and then `catalog-a5-pr-self-review` through `catalog-a13-daily-retro`. Ids a1 through a4 were the same roles as the `cloud-*` family, authored twice because of a historical fork we hadn't cleaned up. Ids a5 through a13 were something different entirely: they were flows, not roles, and the `a5…a13` sprint-label numbering was meaningful to exactly the three people who had been in the room when we picked it.

On top of that, a verb-first `adopt-ship-elmundi` — an onboarding pattern with our pilot org's name hard-coded into its id. It had leaked into the public surface of a library we were asking strangers to browse.

> Two namespaces describing the same SDLC roles is one namespace you can't trust.

From the outside, the library looked like a grab-bag. From the inside, the library looked like two libraries glued together with a couple of post-its stuck on top. Renaming a role would have required thinking about both `cloud-ba` and `catalog-a3-ba-spec`, because both existed, both were in the picker, and whichever one you didn't rename would immediately drift out of sync with the one you did.

The bigger problem was that nothing in the id told you how the pattern was meant to be used. `cloud-ba` could be a lane — wired into a scheduled workflow on issues.labeled — or a one-shot request the operator typed in the Requests UI. There was no metadata answering the question, so both pickers pulled from the same undifferentiated list and the UIs had to guess. The Lanes hub showed patterns that made no sense as lanes. Requests offered patterns that were really includes of other patterns, not standalone invocations.

RFC-0008 is the fix for both of those problems at once. A single canonical namespace, and a `modes` field that says out loud whether the pattern is a lane, a request, or both.

## The boring commit that made the next one possible

`3517bb2 — Catalog cleanup: remove duplicate and org-specific patterns`.

Five patterns deleted:

- `catalog-a1-intake` (duplicate of `cloud-intake`)
- `catalog-a2-clarification` (duplicate of `cloud-clarification`)
- `catalog-a3-ba-spec` (duplicate of `cloud-ba`)
- `catalog-a4-developer` (duplicate of `cloud-developer`)
- `adopt-ship-elmundi` (pilot-org onboarding, never universal)

Twenty-one survivors. All non-duplicate patterns kept. Every reference in the `adoption-minimum` and `web-application` collections got rewired to the `cloud-*` canonical. The `methodology-api` tool was updated, the kickoff prose was updated, the `agent-matrix` and authoring and product-model docs were updated. Canonical `content_sha256` restamped via `scripts/restamp_artifact_shas.py`. 55/55 artifacts canonical. 60/60 CLI tests green. 16/16 catalog tests green.

This is the boring commit. Nothing in the commit message is clever. We want to say plainly: it is the one that made the next commit possible.

You cannot cleanly rename 26 things across 78 files. You can cleanly rename 21. The difference is whether five of those names are aliases of other names in the same rename, because aliases in the middle of a rename are how you end up with commits you have to roll back. We deleted the duplicates first, stopped supporting ElMundi-specific ids in the public catalog, and only then reached for the bigger reform.

Most of the work in `3517bb2` is not about removing patterns. It is about rewiring every other place in the repo that pointed at those patterns and would otherwise break silently. That is the shape of every good cleanup commit we have ever written.

## The rename and the metadata (`e8e6a26`)

`RFC-0008 Phase 0+1: catalog reform — naming, modes, metadata`.

Twenty-one patterns, six categories, one commit. The new shape of an id is `<category>-<name>`, and the categories are:

- `role-*` — seven patterns. The SDLC roles an agent plays on a specific ticket: intake, clarification, BA, developer, QA architect, tech architect, security officer.
- `flow-*` — eight patterns. Self-contained SDLC procedures that run end-to-end and produce an artifact: `flow-pr-self-review`, `flow-check-failure-recovery`, `flow-preview-validation`, `flow-preview-failure-recovery`, `flow-qa-acceptance`, `flow-human-handoff`, `flow-learning-capture`, `flow-daily-retro`.
- `scan-*` — zero patterns. Reserved for the Phase-1 expansion: tech-debt scanner, security-deps audit, docs freshness, API contract diff. The prefix exists before the first pattern does, on purpose.
- `op-*` — two patterns. Automation that keeps Ship itself healthy: `op-workflow-self-heal`, `op-retry-sweep`.
- `onboard-*` — two patterns. One-shot adoption procedures: `onboard-adopt`, `onboard-seed-knowledge`.
- `common-*` — two patterns. Non-executable fragments, included by other patterns via `spec.include`: `common-base`, `common-kickoff`. These never appear in any picker.

{{chart: blog-catalog-rename | 21 patterns, six categories. The empty `scan` bar is not a mistake — the prefix exists so the Phase-1 expansion has a place to land. }}

Every pattern now carries a metadata block in `spec:` that answers the questions the old catalog couldn't:

```yaml
spec:
  install_target: prompts/role/ba.md
  category: role
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: issues.labeled
    pattern: "ready:ba"
  inputs:
    - name: ticket_url
      type: url
      required: true
  enabled_on_install:
    default: false
    presets:
      web-app: true
  knowledge_topics: [code-style, architecture]
```

`category` is the six-way prefix. `modes` is a subset of `{lane, request}` and is the field that lets the UIs filter without guessing — Lanes shows patterns where `modes ∋ lane`, Requests shows `modes ∋ request`, `common-*` patterns carry `modes: []` so neither picker surfaces them. `default_trigger` is the pre-filled lane wiring (schedule cron or event filter). `inputs` is the parameter list the Requests form generator reads. `include` is the list of `common-*` patterns whose bodies get prepended at render time. `lane_workflow` is an optional override for the starter YAML. `enabled_on_install` decides whether a preset's seed bundle wires this pattern up by default. `knowledge_topics` wires the pattern to the knowledge buckets it expects to read from.

That frontmatter is load-bearing for the rest of this post. Everything else falls out of it.

The rename itself ran through two one-shot migration scripts. `scripts/rfc_0008_rename.py` walked the `artifacts/patterns/` tree, moved the directories, rewrote the ARTIFACT.md frontmatter for each one, and injected the new metadata block according to the rename map in the RFC. `scripts/rfc_0008_refs.py` did the sweep — 390 references updated across 78 files: collections, docs, CLI templates, tests, console mocks, backend route code, catalog fixtures. We deliberately excluded `documentation/protocol/rfc-0001.md` through `rfc-0007.md` from the sweep; those are historical record and they should read the way they read the day we wrote them.

Two backend changes made the metadata visible upstream. `backend/app/services/catalog.py` grew a `CatalogArtifact` that exposes every new field as a typed property, and a `list_patterns_by_mode(mode)` helper that the Lanes and Requests routes now call. `backend/app/api/v1/routes/catalog.py` changed the shape of `CatalogEntryOut` to surface `category`, `modes`, `default_trigger`, `resolved_lane_workflow`, `include`, `inputs`, and `enabled_on_install`, so the console drives its UI from a single API response instead of having to synthesise the filter rules client-side.

Validation: 55/55 artifacts canonical after the SHA restamp. 33/33 backend tests green (catalog + lanes sync + manifest). 90/90 CLI tests green across nine suites, including an updated kickoff test that now fetches `common-kickoff` by its new id and help/defaults that advertise the new ids in CLI output. `onboard-seed-knowledge` had its idempotency marker and branch name resynchronised with the new pattern id so the seeding PR still commits `.ship/state/onboard-seed-knowledge.v1.json` on the first run after install.

We want to be clear about what this commit is not. It is not a feature. It is not user-facing in any single way that justifies the 78 files it touches. Every externally-observable consequence of the rename — the filtered pickers, the Requests catalog grid, the seed bundle — either shipped before as something flawed or is still landing in follow-up commits. The only thing `e8e6a26` does in isolation is make the catalog name the things it has in a way that matches what they are.

That is enough, because it is the thing the next commit needs.

## The resolver

One small function deserves its own paragraph, because it is how the console ends up knowing which starter YAML to install when an operator clicks "Add lane" on a pattern card.

`resolve_lane_workflow()` in `backend/app/services/catalog.py` picks the lane's starter workflow from four sources, first non-null wins:

1. Explicit `spec.lane_workflow` in the pattern's frontmatter.
2. `pr-and-ci-gate` when `default_trigger.event` is `pull_request` / `pull_request_target` — those need PR-comment permissions and a PR-scoped context-injection path.
3. `pipeline-self-heal` when the pattern id starts with `op-workflow-` — those need `actions: write` to rewrite CI files.
4. `parallel-audit-lanes` when `category == "scan"` — fans out audits across a matrix and reports differently.
5. `scheduled-sdlc-lane` otherwise — the universal agent-run path, works for schedule and non-PR events.

The route handler surfaces the resolved value on `CatalogEntryOut.resolved_lane_workflow`. The console reads the response, renders the starter YAML id in the Library card, and wires the Advanced → Override control to the per-lane `workflow:` key in `.ship/config.yml`.

That is five lines of precedence and a function that fits on a page. What makes it possible is that `category` and `default_trigger` are now first-class metadata. In the old catalog you would have had to pattern-match on the id — `startswith("catalog-a7")`, `startswith("cloud-workflow-")` — and the pattern would be wrong the moment somebody added a new pattern that did not fit the historical id shape. We are not proud of how many places in the old backend did that. We are proud of how few places in the new backend have to.

## Why the matrix lane fell out of the rename (`80e3431`)

`RFC-0008 C3.1+C3.2a+C3.2b: multi-pattern lanes (schema v2.1 + fanout)`.

RFC-0006 had fixed lanes at one pattern each: `lanes.<id>.pattern: <pattern-id>`, a scalar, one-to-one. That shape had always been provisional. The retired `DefaultPipelineSpec` already treated a "tech debt" lane as three parallel role runs, and the Phase-1 expansion has more bundles where the natural unit is "a trigger, fanning out over a set of patterns" — a release hardening lane running `scan-security-deps` plus `scan-api-contract`, or a weekly audit lane running three scanners in parallel.

Schema v2.1 promotes the list:

```yaml
lanes:
  tech_debt_audit:
    schedule: "0 6 * * 1"
    patterns:
      - role-tech-architect
      - role-qa-architect
      - role-security-officer
    fanout: matrix
```

`patterns: [ids]` is the canonical shape. `pattern: <id>` stays as a single-string alias so existing configs see a zero-diff upgrade — single-pattern lanes keep the scalar form on write, the emitter only produces the inline list when the lane has ≥2 patterns. Exactly one of the two keys is required per lane; sending both is rejected with `invalid_pattern_shape` at parse time.

`fanout` is the new key, and it is the interesting one. Three values:

- **`matrix`** (default). Each pattern gets its own GitHub Actions job, parallel, with its own logs, its own artifacts (`ship-prompt-<lane>-<pattern>`), its own token. Isolation is per-job. If one pattern in the bundle crashes, the other two still produce their output and the aggregate job still calls back to Ship.
- **`sequential`**. One GHA job, `shipctl run` iterates the patterns in the order they appear. Slower, but simpler to read, and the only safe choice when the patterns have ordering constraints between them.
- **`concurrent`**. One GHA job, `shipctl run` spawns subprocesses in parallel. Cheaper than matrix for small, fast patterns — no new job container per pattern — but the logs are interleaved and the isolation is whatever the host OS gives you.

`run-agent.yml` is the workflow that actually dispatches a lane. Under RFC-0006 it was two jobs, `prepare` and `run`, one invocation per lane. Under RFC-0008 C3.2 it is three jobs: `plan → run → aggregate`. The plan job asks `shipctl` for the lane's patterns and its resolved fanout mode. The run job uses a dynamic `fromJSON` matrix — one element per pattern when fanout is `matrix`, a single-element matrix otherwise. The aggregate job collapses per-job outcomes into one callback to Ship, so the backend still sees exactly one `pipeline_run` per lane. Multi-pattern at the runtime layer did not leak into the observability layer. That was the design constraint.

`shipctl run` learned two new flags. `--pattern <id>` picks a specific member of a multi-pattern lane (that's what the matrix calls into). `--fanout <mode>` overrides the resolved mode. Internally, `shipctl` emits one pattern body per invocation when called from the matrix, or multiple banner-separated bodies stitched into a single agent run when fanout is `sequential` or `concurrent`. Either way, `shipctl` writes exactly one lane-scoped idempotency marker and sends exactly one aggregate callback. `shipctl lanes list --json` now exposes the resolved fanout in its output so dashboards and debug tooling can see what mode a lane ended up in without re-reading the config.

Helpers to keep the two shapes from bleeding across call sites. `cli/lib/config/schema.mjs` exports `lanePatterns(lane): string[]` and `lanePrimaryPattern(lane): string | null`. Every read-side call site in the CLI, the renderer, and the dashboard goes through those helpers instead of branching on `lane.pattern` vs `lane.patterns`. `backend/app/services/lanes_sync.py` does the same normalisation when parsing `.ship/config.yml` into DB state — writes the full list into `Lane.config_blob.patterns`, keeps `Lane.pattern` = first element for single-pattern consumers until C3.4 renames `kind` to `lane_id`.

The backend also learned two small manners. `LaneTriggerIn` grew a `fanout` field. `propose_repo_config` only emits the `fanout` key when the lane has ≥2 patterns and the mode is not the default `matrix` — we do not want to churn YAML diffs with a field that has no observable effect. The linter follows the same rule: setting `fanout` on a single-pattern lane is a warning, not an error, so schedule templates that declare it blindly remain portable.

Tests: 166 CLI, 545 backend, all green.

Now the part that matters. None of the above was possible under the old naming. `catalog-a1-intake` and `cloud-intake` both existed. If a multi-pattern lane listed `cloud-intake`, which pattern did the operator actually mean? You could argue it either way, and any argument you picked was going to surprise somebody. Multi-pattern lanes require that every member id resolve to exactly one pattern body. The rename is the thing that made every id resolve to exactly one pattern body.

> You cannot fan out over a list you cannot uniquely name.

That sentence is the whole relationship between Phase 0+1 and C3.2. The schema change — one field `patterns: []` instead of `pattern: "..."` — was tiny. The execution model change — three fan-out modes, a dynamic matrix, an aggregate step, new CLI flags, new helpers on both sides — was substantial. Neither of them was on the table before the rename, because before the rename the catalog wasn't a set of uniquely-named patterns. It was a set of patterns with duplicates, a set of patterns with an inherited sprint-label numbering scheme nobody outside the room understood, and a set of patterns whose id did not tell you how they were meant to be invoked. You cannot define a lane as "a list of these" when "these" is not a closed, canonical vocabulary.

## The lesson that matters

Most of what looked like product design on Apr 22 was cleaning up the namespace the product lives in.

The Lanes hub now shows one row per pattern, filtered by `modes ∋ lane`, grouped by category. That reads like UI work. The underlying commit is a metadata rename. The Requests form now generates dynamic fields from `pattern.inputs`. That reads like feature work. The underlying commit is the same metadata rename, reading a different field. The multi-pattern matrix lane reads like runtime work, and in its internal plumbing it is — but the reason we could even propose it is the rename, because the rename is what made a pattern id a thing you could put in a list.

This is the pattern we keep learning, in public, in the open-source repo, on back-to-back days: catalog reform is a prerequisite to runtime expressiveness, not a sibling of it. You do not get to add a feature that iterates over your vocabulary until the vocabulary is canonical. Every system we have shipped that tried to paper over a messy vocabulary with clever runtime logic — fuzzy id matching, id aliases, "helpful" normalisation at the parse boundary — has ended up with the ambiguity showing up at runtime anyway, in a place where the user is the one who has to resolve it.

The boring commit is `3517bb2`. It deleted five names. The less boring commit is `e8e6a26`. It renamed 21 names and added metadata that describes their shape. The interesting commit is `80e3431`, and its schema change is one field. But neither of the last two would have been possible without the first, and the first was possible because we gave ourselves permission to say "before we extend the catalog, we are going to clean it up," and not to treat that as a holding pattern between real pieces of work.

The catalog is now one place, with one naming scheme, with metadata that the UIs and the runtime can both read without guessing. A lane can be a bundle of patterns. Each member of the bundle resolves to exactly one body. The fan-out mode is a property of the lane, not a property of the dispatcher. The backend sees one `pipeline_run` per lane no matter how many patterns ran underneath. The console drives its UI from one API response. The CLI writes the same shape that the backend parses. That is not six features — it is one feature, which is that the catalog names its contents correctly, stated six times through six consumers.

Most of what looks like product design is cleaning up the namespace your product lives in. We will say this again, probably soon, because we will forget it again, probably sooner. Next time we are staring at a migration that feels like paperwork, we are going to look at this week's three commits and remember that the runtime change we actually wanted was a rounding error on top of the rename, and the rename was the whole job.
