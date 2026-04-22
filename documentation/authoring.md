# Authoring artifacts

This page is the contributor's reference for adding a new artifact (or adapter)
to the Ship catalog. The catalog itself is documented elsewhere — `/patterns`,
`/tools`, `/collections` for *what exists*; this page is about
*how to add a new one*. The normative shape is defined in
[RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2) (folder layout,
front-matter, hashing) and [RFC-0004](/docs/protocol/rfc-0004-adapters)
(adapter sections); read them before changing schema. Vocabulary like *kind*,
*pattern*, *preset*, and *channel* is defined in [Concepts](/docs/concepts).

> **Before you draft a new pattern**, read
> [Pattern vs knowledge](/docs/authoring/pattern-vs-knowledge) — the editorial
> rubric that decides whether what you have in mind is a `pattern` or
> belongs in a **knowledge bucket** (see
> [Knowledge buckets](/docs/knowledge-buckets) for the shipped model).
> The single biggest cause of pattern bloat is reference material filed
> as method.

## Folder layout

Every artifact is a folder under the `artifacts/` root, one per kind:

```
artifacts/
├── patterns/
│   └── <id>/
│       ├── ARTIFACT.md          # required — frontmatter + body
│       ├── examples/            # optional — runnable or readable examples
│       ├── reference/           # optional — deep-dives a body can link to
│       ├── scripts/             # optional — helpers (e.g. verify-branch.mjs)
│       ├── tests/               # optional — eval fixtures (golden.yaml)
│       ├── i18n/                # optional — localised ARTIFACT.md siblings
│       └── CHANGELOG.md         # recommended once version ≥ 1.0.0
├── tools/<id>/ARTIFACT.md
└── collections/<id>/ARTIFACT.md
```

Naming rules:

- `<id>` is **kebab-case**, ≤ 64 characters, unique within its kind. The
  folder name and the `id:` field in front-matter must match — the CLI's
  filesystem index uses the folder name as the cache key
  (`artifacts/<plural>/<id>/ARTIFACT.md`).
- The artifact body lives in `ARTIFACT.md` *only*. Sibling files exist for
  examples, references, scripts, tests, translations, and the changelog —
  not for splitting the body across files.
- The whole folder participates in `content_sha256` (everything except
  `CHANGELOG.md` and the `content_sha256` field itself), so adding
  `examples/foo.md` is a real change and must come with a version bump.

Names of actual ids in the repo today: `role-developer`, `common-base`,
`flow-daily-retro`, `scan-tech-debt`, `op-workflow-self-heal`,
`onboard-adopt` (patterns); `linear`, `playwright`, `github-actions`
(tools); `preset-web-app`, `agent-rules-cursor`, `addendum-pharma`,
`web-application` (collections). Pattern ids all follow
[RFC-0008](/docs/protocol/rfc-0008-catalog-reform)'s
`<category>-<name>` scheme — `role`, `flow`, `scan`, `op`, `onboard`,
or `common`.

## Front-matter contract

Every `ARTIFACT.md` opens with a YAML block delimited by `---` on its own
lines. The same **base** fields apply to every kind; the `spec:` mapping is
kind-specific. Tables below list only fields that the parser at
`cli/lib/artifacts/fs-index.mjs` reads or that RFC-0005 normatively requires.

### Base fields (all kinds)

| Field            | Req | Type    | Purpose                                                                                       | Example                                                                                  |
|------------------|-----|---------|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| `artifact_kind`  | yes | enum    | One of `pattern`, `tool`, `collection`. Must match the parent folder. (`workflow` was retired by RFC-0007.) | `pattern`                                                                                |
| `id`             | yes | string  | Kebab-case slug, ≤ 64 chars, unique within kind, equal to the folder name.                    | `role-developer`                                                                        |
| `name`           | yes | string  | Human title, ≤ 80 chars. Rendered as the `title` in list responses.                           | `Developer`                                                                              |
| `description`    | yes | folded  | SKILL.md-style: third person, what + when, ≤ 1024 chars, includes at least one trigger term. | `Implementation role: branch contract, PR shape, evidence. Use when an agent picks…`     |
| `version`        | yes | semver  | `MAJOR.MINOR.PATCH`, no `v` prefix. Bumped whenever any byte in the folder changes.           | `1.4.2`                                                                                  |
| `channel`        | yes | enum    | `stable`, `edge`, or `experimental`. Clients filter by `api.channel`.                         | `stable`                                                                                 |
| `min_shipctl`    | yes | semver  | Minimum `shipctl` version that can consume this artifact.                                     | `0.3.0`                                                                                  |
| `updated_at`     | yes | ISO-8601| UTC timestamp of the last publish for this version.                                           | `"2026-04-12T04:11:35+03:00"`                                                            |
| `content_sha256` | yes | hex     | Merkle hash over the folder contents. Written by lint, never by hand.                         | `9f1c0a…d7`                                                                              |
| `deprecated`     | yes | bool    | `true` if kept for reference but should not be adopted.                                       | `false`                                                                                  |
| `replaced_by`    | yes | string\|null | New `id` to migrate to when `deprecated=true`.                                            | `null`                                                                                   |
| `yanked`         | yes | bool    | `true` to permanently withdraw a version (server returns `410 Gone`).                         | `false`                                                                                  |
| `group`          | yes | string  | Logical grouping for catalog UI. For patterns it mirrors [RFC-0008](/docs/protocol/rfc-0008-catalog-reform) `spec.category` (`role`, `common`, `flow`, `scan`, `op`, `onboard`); tools use `tracker`, `ci`, `e2e`, `platform`; `agent-rules` collections use `agent-rules`. | `role`                                                                                   |
| `tags`           | yes | string[]| Flat list of discovery tags. Used by `shipctl search` and the catalog filter.                 | `[implementation, pr]`                                                                   |
| `authors`        | yes | string[]| Owners (`@org/team`); the lint refuses an empty list.                                         | `[@elmundi/ship-core]`                                                                   |
| `license`        | yes | string  | SPDX identifier.                                                                              | `Apache-2.0`                                                                             |
| `spec`           | yes | mapping | Kind-specific block — see sub-sections below.                                                 | (see below)                                                                              |

### `spec` for `pattern`

The [RFC-0008](/docs/protocol/rfc-0008-catalog-reform) metadata block
is the source of truth for a pattern's identity and invocation shape.
Required fields for executable patterns (categories `role`, `flow`,
`scan`, `op`, `onboard`); `common-*` patterns are fragments —
`modes: []` and usually no `default_trigger` / `inputs`.

| Field                | Req               | Type      | Purpose                                                                                                                                                      | Example value (`role-developer`)                                       |
|----------------------|-------------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| `install_target`     | yes               | path      | Where the rendered body lands when an agent installs the prompt locally.                                                                                     | `prompts/role/developer.md`                                            |
| `category`           | yes               | enum      | `role \| flow \| scan \| op \| onboard \| common` — drives UI grouping, the lane-workflow resolver, and id-prefix conventions.                                | `role`                                                                 |
| `modes`              | yes               | string[]  | Invocation shape. Non-empty unless `category=common`; values `lane` and/or `request`. `common-*` patterns declare `modes: []` — they're only pulled via `spec.include`. | `[lane, request]`                                                      |
| `include`            | no                | string[]  | Pattern ids whose bodies are composed into the host pattern's prompt at render time. Canonical way to share `common-*` preambles. Max depth 2, cycles raise. | `[common-base]`                                                        |
| `default_trigger`    | yes when `lane`   | mapping   | Suggested lane wiring, pre-filled in the Library UI. `kind: event \| schedule` with the kind-specific keys; see [RFC-0008 § Metadata schema](/docs/protocol/rfc-0008-catalog-reform#metadata-schema). | `{kind: event, event: issues.labeled, pattern: "ready:developer"}`     |
| `inputs`             | yes when `request`| object[]  | Named parameters when dispatched as a request; drives the Requests form. Each entry: `name`, `type` (`text \| textarea \| url \| enum \| bool \| ref`), `required?`, `hint?`, `values?`, `default?`. Replaces the legacy bare-id list. | `[{name: issue_url, type: url, required: true, hint: "Issue URL"}]`    |
| `enabled_on_install` | no                | mapping   | Which patterns the seed bundle wires per preset. `{default: bool, presets: {<preset>: bool}}`.                                                                | `{default: true, presets: {web-app: true, api-backend: true}}`         |
| `lane_workflow`      | no                | string    | Override the starter YAML the Pipeline installs. When unset the resolver picks from `category` + `default_trigger` (see below).                              | `scheduled-sdlc-lane`                                                  |
| `knowledge_topics`   | no                | string[]  | Bucket topics this pattern may consult at render time — the join key with [knowledge buckets](/docs/knowledge-buckets).                                       | `[code-style, architecture]`                                           |
| `role`               | no                | string    | If the pattern is a role slot, the role id (`developer`, `ba`, `intake`, …).                                                                                 | `developer`                                                            |
| `template`           | no                | bool      | `true` when the body uses `{{ISSUE}}`, `{{BASE}}`, or other interpolations.                                                                                  | `true`                                                                 |
| `triggers`           | no                | string[]  | Discoverability cues in addition to `default_trigger` (state names, label names, role names).                                                                | `[linear-state:Ready, label:agent:developer]`                          |
| `outputs`            | no                | string[]  | Named artifacts the pattern produces.                                                                                                                        | `[pull-request, evidence-comment]`                                     |
| `evals`              | no                | path      | Path inside the folder to a golden eval fixture.                                                                                                             | `tests/golden.yaml`                                                    |

The starter YAMLs referenced by `lane_workflow` (`scheduled-sdlc-lane`,
`pr-and-ci-gate`, `parallel-audit-lanes`, `pipeline-self-heal`) live in
`backend/app/resources/starter_workflows/` and are installed by the
Pipeline flow — they are never authored as catalog artifacts. When
`spec.lane_workflow` is unset the backend resolves the default per
[RFC-0008 § Lane workflow resolution](/docs/protocol/rfc-0008-catalog-reform#lane-workflow-resolution).

### `spec` for `tool`

| Field            | Req | Type      | Purpose                                                                                | Example value (`linear`)                          |
|------------------|-----|-----------|----------------------------------------------------------------------------------------|---------------------------------------------------|
| `capability`     | yes | enum      | One of `tracker`, `ci`, `e2e`, `agents`, `platform`. Drives the capability-five map.   | `tracker`                                         |
| `install_target` | yes | path      | Where the integration note lands when copied into a downstream repo.                   | `documentation/tools/integrations/linear.md`      |
| `vendor_neutral_id` | no | string  | The contract id this tool implements (e.g. `tracker-contract` for any tracker).        | `tracker-contract`                                |
| `interfaces`     | no  | string[]  | Declared surfaces (`graphql-api`, `web-app`, `cli`, `webhook`).                        | `[graphql-api, web-app]`                          |
| `auth`           | no  | string[]  | Auth modes the adapter supports.                                                       | `[api-key, oauth]`                                |
| `contracts`      | no  | string[]  | Capability contracts the tool fulfils.                                                 | `[issue-state, label-set, evidence-comment]`      |

### `spec` for `workflow` — retired

`artifact_kind=workflow` was removed in
[RFC-0007 Phase 6](/docs/protocol/rfc-0007-lanes-and-run-agent) and the
catalog folder `artifacts/workflows/` is gone. Authors never draft a
workflow artifact — ever. The four starter YAMLs
(`scheduled-sdlc-lane`, `pr-and-ci-gate`, `parallel-audit-lanes`,
`pipeline-self-heal`) live inside
`backend/app/resources/starter_workflows/` and are installed through
the Pipeline flow only. Per
[RFC-0008](/docs/protocol/rfc-0008-catalog-reform), the Pipeline picks
the right starter from a pattern's `category` + `default_trigger`;
override on a per-pattern basis by setting `spec.lane_workflow` in the
pattern's frontmatter. Customer cadences live as
[lanes](/docs/concepts#lane) in `.ship/config.yml` (v2); see
[Lanes](/docs/lanes) for the operator surface.

### `spec` for `collection`

| Field                    | Req | Type      | Purpose                                                                                      | Example value (`preset-web-app`)                                                       |
|--------------------------|-----|-----------|----------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| `subkind`                | yes | enum      | `preset`, `addendum`, `starter`, or `agent-rules`. Drives how the collection is consumed.    | `preset`                                                                               |
| `install_target`         | yes | path      | Where the collection's installer doc lands in a downstream repo.                             | `documentation/collections/preset-web-app.md`                                          |
| `preset_id`              | preset | string | Stable id used by `shipctl init --preset <id>` and `stack.preset` in `.ship/config.yml`.     | `web-app`                                                                              |
| `compatible_trackers`    | preset | string[] | Tracker ids the preset accepts.                                                            | `[linear, jira, github-issues]`                                                        |
| `compatible_ci`          | preset | string[] | CI ids the preset accepts.                                                                 | `[gh-actions, gitlab-ci, circleci, azure-pipelines, manual]`                           |
| `compatible_agents`      | preset | string[] | Agent ids the preset accepts.                                                              | `[cursor, codex, claude, aider, copilot]`                                              |
| `required_tools`         | preset | string[] | Slot list — `<current>` placeholders resolve from `stack.*`.                              | `[tool/tracker/<current>, tool/ci/<current>, tool/playwright]`                         |
| `optional_tools`         | preset | string[] | Slots a downstream may opt into.                                                          | `[tool/preview/vercel, tool/flags/launchdarkly]`                                       |
| `addendums`              | preset | string[] | Addendum ids the preset declares — usually empty; downstream opts in via pin.              | `[]`                                                                                   |
| `addendum_id`            | addendum | string | Stable addendum id.                                                                        | `pharma`                                                                               |
| `applies_to`             | addendum | string[] | Preset ids this addendum can layer onto.                                                  | `[mobile-app, web-app, api-backend]`                                                   |
| `regulatory_frameworks`  | addendum | string[] | Frameworks the addendum encodes (HIPAA, GDPR, …).                                         | `[HIPAA, GDPR, 21-CFR-Part-11, EU-AI-Act]`                                             |
| `composes`               | starter | mapping  | Bundled artifact ids grouped by kind: `patterns: [...]`, `tools: [...]`. | `{patterns: [role-developer], tools: [linear]}`     |

`agent-rules-*` collections set `subkind: agent-rules` and ship the rule body
inside `ARTIFACT.md`. Their on-disk install path (e.g.
`.cursor/rules/ship-artifacts-protocol.mdc`, `CLAUDE.md`,
`.github/copilot-instructions.md`) is documented in the body itself, with
the `MARKER` / `END_MARKER` pair `<!-- ship-cli: artifacts-protocol v1 -->`
… `<!-- ship-cli:end artifacts-protocol -->` so `shipctl sync` can refresh
the block in place. The `spec.install_target` for an `agent-rules`
collection points at the docs page that explains where to install the
block, not at the on-disk rule file.

## Authoring a `pattern`

Worked example: re-author `role-developer` from scratch.

1. **Pick the id and folder.** Slug is `role-developer`. Create
   `artifacts/patterns/role-developer/ARTIFACT.md`.
2. **Draft the front-matter.** Use the table above. The fields that change
   most between patterns are `name`, `description`, `tags`, `group`, and
   the `spec` block (`category`, `modes`, `default_trigger`, `inputs`,
   `install_target`, `role`). Match the live shape of
   [`role-developer`](/patterns/role-developer):

   ```yaml
   ---
   artifact_kind: pattern
   id: role-developer
   name: Developer
   version: 1.0.0
   channel: stable
   min_shipctl: 0.3.0
   updated_at: "2026-04-19T00:00:00Z"
   content_sha256: <auto>
   deprecated: false
   replaced_by: null
   yanked: false
   group: role
   tags: [implementation, pr]
   authors: [@elmundi/ship-core]
   license: Apache-2.0
   description: >-
     Implementation role: branch contract, PR shape, evidence. Use when
     an agent picks a cloud-agent slot in a Ship lane, when wiring this
     prompt into a scheduled workflow, or when the catalog tags
     (implementation, pr) match the current task.
   spec:
     install_target: prompts/role/developer.md
     category: role
     modes: [lane, request]
     include: [common-base]
     default_trigger:
       kind: event
       event: issues.labeled
       pattern: "ready:developer"
     inputs:
       - name: issue_url
         type: url
         required: true
         hint: "Issue URL"
     enabled_on_install:
       default: true
       presets:
         api-backend: true
         mobile-app: true
         monorepo: true
         web-app: true
     template: true
     role: developer
   ---
   ```

3. **Write the body.** Keep one prompt per file. The body is what the agent
   reads at runtime; everything else is metadata. Include:

   - `# Role: …` heading and a one-line statement of what the role owns.
   - A `## Context` block listing the variables the runtime injects
     (`{{ISSUE}}`, `{{TITLE}}`, `{{DESCRIPTION}}`, `{{BASE}}`).
   - A `## Task` numbered list with the contract: branch name, test
     requirements, commit style, PR shape, evidence comment marker.

   Leave out anything that belongs to the common-base guardrails (those live
   in `common-base` and are interpolated as `{{BASE}}`), and anything
   org-specific (URLs, image names, host names — those are reference, not
   methodology).

4. **Test locally.** From the Ship repo root:

   ```bash
   shipctl pattern show role-developer        # reads from artifacts/patterns/role-developer/
   shipctl pattern list | rg role-developer
   shipctl verify --check artifacts-up-to-date # confirms no drift vs the API
   shipctl knowledge init --workspace <id>    # seeds .ship/knowledge/ starters in a tenant repo
   ```

   `shipctl knowledge init` exercises the knowledge-bucket seed flow
   that feeds `spec.knowledge_topics`; see
   [Knowledge buckets](/docs/knowledge-buckets) for the full bucket
   surface and `cli/lib/commands/knowledge.mjs` for the live
   subcommand contract.

5. **Contribute back.** Branch, PR, request review from `@elmundi/ship-core`.
   The lint will fail the PR if `content_sha256` is stale, the description
   misses a trigger term, or the version was not bumped on a body change.

## Authoring a `tool`

A `tool` artifact describes an integration adapter — what capability it
fills and how downstreams wire it. The catalog tool is the *narrative*; the
runtime adapter under `cli/lib/adapters/` is the *executable* counterpart
(see [Authoring an adapter](#authoring-an-adapter)).

Worked example: `linear`.

1. **Pick the id and capability.** `linear` lives at
   `artifacts/tools/linear/ARTIFACT.md`. Capability is `tracker` (one of
   `tracker | ci | e2e | agents | platform` — adding a sixth requires an
   RFC).

2. **Draft front-matter** with the `tool` `spec`:

   ```yaml
   spec:
     capability: tracker
     install_target: documentation/tools/integrations/linear.md
   ```

   Add `vendor_neutral_id`, `interfaces`, `auth`, and `contracts` when the
   tool implements a capability contract another tool could substitute for
   (e.g. `linear`, `jira`, and `github-issues` all implement
   `vendor_neutral_id: tracker-contract`).

3. **Write the body** for an integrator, not an end user. Sections that
   work today: `## What you wire` (projects, states, labels, env vars),
   `## Agent touchpoints` (where pick scripts call in, what idempotency the
   agent expects), `## Read next` (links to the contract page and example
   wiring). Do not paste credentials, org slugs, or workflow filenames —
   those belong in adoption notes.

4. **Test locally.**

   ```bash
   shipctl tool show linear
   shipctl tool list | rg linear
   ```

5. **Contribute back.** If the tool also needs detection in
   `shipctl init --bootstrap`, ship the adapter change in the same PR
   under `cli/lib/adapters/trackers/linear.mjs`.

## Authoring a `workflow` — retired

`artifact_kind=workflow` is not a catalog kind. Removed by
[RFC-0007 Phase 6](/docs/protocol/rfc-0007-lanes-and-run-agent); the
four starter YAMLs now live in
`backend/app/resources/starter_workflows/` and are installed only
through the Pipeline flow. Nothing else to draft — if you need a new
cadence:

1. Author the prompt as a **pattern** under
   `artifacts/patterns/<id>/ARTIFACT.md` with the full
   [RFC-0008](/docs/protocol/rfc-0008-catalog-reform) `spec` block
   (`category`, `modes`, `default_trigger`, `inputs` when appropriate).
2. Reference that pattern from a **lane** in the customer's
   `.ship/config.yml` (v2); `shipctl lanes install` renders the thin
   `.github/workflows/ship-<lane>.yml` wrapper.
3. If the default starter isn't right, set `spec.lane_workflow` on the
   pattern or use the console's "Advanced → Override" on the lane
   itself — no new catalog artifact is needed.

## Authoring a `collection`

A collection is a curated bundle. There are four `subkind`s in the schema:
`preset`, `addendum`, `starter`, and `agent-rules`. Pick the right one
before drafting front-matter.

### `subkind: starter` — `web-application`

A starter is the easiest case: it composes ids that already exist.
Worked example: `web-application` today lists tools / patterns in the
body prose and sets `spec.subkind: starter`. For **new** starters use
the normative `spec.composes` mapping instead of body prose:

```yaml
spec:
  subkind: starter
  install_target: documentation/collections/web-application.md
  composes:
    patterns: [role-developer, role-qa-architect, flow-pr-self-review]
    tools:    [linear, github-actions, playwright]
```

The explicit mapping is what `shipctl init` and the catalog index
consume; body prose is narrative only. Migrate `web-application` to
`spec.composes` when you touch it next.

### `subkind: preset` — `preset-web-app`

A preset is consumed by `shipctl init --preset <preset_id>` and pinned in
`.ship/config.yml` as `stack.preset: <preset_id>`. Required `spec` keys
are `preset_id`, `compatible_trackers`, `compatible_ci`,
`compatible_agents`, `required_tools`, `optional_tools`, `addendums`, and
`install_target`. Slot syntax: `<current>` resolves to whichever adapter
the downstream selected (`tool/tracker/<current>` becomes
`tool/tracker/linear` once `stack.tracker = linear`). The body is the
operator's view of the preset: SDLC columns, label contract, CI stages,
evidence types, promote gates, required secrets (generic names only),
recommended addendums.

### `subkind: addendum` — `addendum-pharma`

An addendum *layers on top of* a preset; it tightens or annotates rules
the base preset already enforces, and never relaxes them. Required `spec`
keys: `addendum_id`, `applies_to` (preset ids), `regulatory_frameworks`,
`install_target`. Downstream opts in by pinning
`collection/addendum-<id>: <version>` in `.ship/config.yml`, or via
`shipctl init --addendum <id>`.

### `subkind: agent-rules` — `agent-rules-cursor`

An `agent-rules` collection ships the **Ship artifacts protocol rule body**
in a shape one agent can install in place. The on-disk install path is
written in the body itself, e.g. `.cursor/rules/ship-artifacts-protocol.mdc`
for `agent-rules-cursor`, `CLAUDE.md` for `agent-rules-claude`,
`.github/copilot-instructions.md` for `agent-rules-copilot`. The body wraps
the protocol with the marker pair

```
<!-- ship-cli: artifacts-protocol v1 -->
…protocol body…
<!-- ship-cli:end artifacts-protocol -->
```

so `shipctl sync` can refresh the block without disturbing surrounding
rules. The marker contract lives in `cli/lib/templates.mjs` (constants
`MARKER` and `END_MARKER`); reuse that module when the rule body is
generated rather than hand-typed. To add a new agent-rules collection:
copy `agent-rules-cursor`, change `id`/`name`/`description`, rewrite the
"Install target:" line and the front-matter the rule file expects, keep
the marker block identical.

## Authoring a preset

A preset is `subkind: preset` inside a `collection` artifact at
`artifacts/collections/preset-<id>/`. The supported preset list as of
today, mirrored in `cli/lib/config/schema.mjs` (`PRESETS`), is:

`web-app`, `api-backend`, `mobile-app`, `cli`, `monorepo`,
`adoption-minimum`.

Add a *new* preset (rather than extending an existing one) when the bounded
context changes — "the install" for mobile-app vs "the browsing session"
for web-app vs "the request" for api-backend. Extend an existing preset
when the change is a new optional gate, label, or addendum.

Checklist for a new preset:

1. Add the new id to `PRESETS` in `cli/lib/config/schema.mjs`.
2. Create `artifacts/collections/preset-<id>/ARTIFACT.md` with
   `subkind: preset` and the full preset `spec` block.
3. Document the product shape, SDLC columns the preset expects, label
   contract, CI stages, evidence types, promote gates, required secrets
   (generic names), and recommended addendums in the body.
4. Reference the preset from the relevant agent-rules and starter
   collections so it is discoverable.
5. Add a unit case under `cli/tests/init.test.mjs` exercising
   `shipctl init --preset <id> --dry-run`.

## Authoring an adapter

Adapters are the executable counterparts of `tool` and `agent-rules`
artifacts. They live under `cli/lib/adapters/<class>/<id>.mjs`, one ESM
module per id, in four classes:

| Class      | Path                                | Examples                                                  |
|------------|-------------------------------------|-----------------------------------------------------------|
| `trackers` | `cli/lib/adapters/trackers/`        | `linear.mjs`, `jira.mjs`, `github-issues.mjs`, `none.mjs` |
| `ci`       | `cli/lib/adapters/ci/`              | `gh-actions.mjs`, `gitlab-ci.mjs`, `manual.mjs`           |
| `language` | `cli/lib/adapters/language/`        | `ts.mjs`, `py.mjs`, `go.mjs`, `rust.mjs`                  |
| `agents`   | `cli/lib/adapters/agents/index.mjs` (delegates to `cli/lib/detect.mjs`) | `cursor`, `codex`, `claude-md`, `copilot`, `aider`, …    |

Each adapter module exports `id`, `kind`, and three async hooks that match
[RFC-0004](/docs/protocol/rfc-0004-adapters):

```js
export const id = "linear";
export const kind = "tracker";

export async function detect(cwd) {
  // returns { present: bool, confidence: 0..1, evidence: [...] }
}
export async function bootstrap() { /* … */ }
export async function verify()    { /* … */ }
```

How `detect(cwd)` is wired:

- Modules use the helpers in `cli/lib/adapters/_fs.mjs`
  (`readEnvFiles`, `readGithubWorkflows`, `pkgDeps`, `exists`, `isDir`,
  `isFile`) to look for marker files, env vars, and dependency entries
  without imposing extra runtime deps.
- The category registry in `cli/lib/adapters/index.mjs` calls every
  adapter in parallel via `detectAll(cwd)`, and `shipctl init` /
  `shipctl doctor` consume the sorted-by-confidence result to propose a
  `stack.*` block.
- Agent detection is delegated to `cli/lib/detect.mjs` (see
  `detectAgentTargets` and `KNOWN_AGENTS`); the agent adapter wrapper
  lives at `cli/lib/adapters/agents/index.mjs`.

How adapters declare cross-adapter `requires`:

- Per RFC-0004, the dependency is declared in the **adapter artifact's
  front-matter** as `requires: ["tool/ci/gh-actions@>=1.2.0", ...]`. The
  CLI resolves them in topological order before any file is written.
- A `## Patch` section in the artifact body lets one adapter append into a
  file another adapter created (with a `marker="ship-managed:<id>"` so
  re-runs are idempotent and ownership is reviewable).

Where adapter tests live:

- Detection: `cli/tests/detect.test.mjs`.
- Bootstrap / init flow: `cli/tests/init.test.mjs`,
  `cli/tests/init-help.test.mjs`, `cli/tests/new.test.mjs`.
- Doctor / verify checks: `cli/tests/doctor.test.mjs`,
  `cli/tests/verify.test.mjs`. The check modules themselves live under
  `cli/lib/verify/checks/` (`agents-on-disk.mjs`, `tracker-labels.mjs`,
  `bootstrap-files.mjs`, …); add a fixture under the same directory
  pattern the existing checks use.

## Local testing

Concrete commands you can run from the Ship repo root or against a fixture
repo:

```bash
# 1. Show an artifact straight from the local filesystem index.
shipctl pattern show role-developer
shipctl tool show linear
shipctl collection show preset-web-app

# 2. Dry-run init against a scratch fixture (no files written).
mkdir -p /tmp/ship-fixture && cd /tmp/ship-fixture && git init -q
shipctl init --dry-run --preset web-app --tracker linear --ci gh-actions \
  --agents cursor,codex --language ts

# 3. Validate config + adapter detection on a real repo.
shipctl doctor
shipctl verify --check stack-enums,bootstrap-files,artifacts-up-to-date

# 4. Run the CLI test suite (covers fs-index, init, doctor, verify, sync).
npm --prefix cli test
```

`shipctl verify` and `shipctl doctor` are how you confirm the
front-matter, the cache, the bootstrap files, and the agent rule markers
all line up. Run both before you ask for review.

## Contributing back

- **Branch** off `main` with `feat/<artifact-id>` or
  `fix/<artifact-id>`. One artifact per PR is preferred; mixed PRs slow
  review.
- **Commits.** Conventional Commits style:
  `feat(patterns): add role-tech-architect`,
  `fix(tools): tighten linear adapter detect`,
  `docs(authoring): clarify preset extension`. Reference the artifact id
  in the scope so the changelog generator can group changes.
- **Semver bumps.** Per RFC-0001 §Version bump rules:
  - **MAJOR** for breaking semantic changes (renaming a role, inverting a
    gate, changing a contract field, flipping `spec.modes`, removing an
    id from `spec.include`).
  - **MINOR** for additive changes (new optional section, new tag, a new
    evidence requirement that is compatible with earlier behaviour, a
    new entry in `spec.include`, adjusting `spec.default_trigger` to a
    broader filter).
  - **PATCH** for clarifications (typo, link fix, example tweak).
  "Body change" now includes the composed body after `spec.include` is
  resolved — changing a `common-*` fragment bumps every host that
  includes it, and changing `spec.default_trigger` counts as a
  behaviour change because it re-wires the Library default. Bumping
  `version` without a body change is rejected by lint; changing the
  body without bumping `version` is rejected too — the
  `content_sha256` over the folder is the source of truth.
- **Reviewer checklist.** Maintainers look for:
  - `id`, folder name, and `artifact_kind` agree.
  - `description` is third person, contains a *what* and a *when*, and
    fires at least one trigger term.
  - `spec` block matches the kind's contract (no unknown required fields,
    no missing required ones).
  - Body contains no org-specific URLs, image names, secret values, or
    Linear/JIRA project ids.
  - Tests added when the change touches detection, bootstrap, or the
    sync path.
- **Channel rollout.** New artifacts land on `channel: edge`. After at
  least one release cycle of operator feedback they are promoted to
  `channel: stable` (`stable` is what `shipctl init` defaults to via
  `api.channel`). Yanking a broken version flips `yanked: true` and the
  API returns `410 Gone`; deprecating a superseded artifact flips
  `deprecated: true` and sets `replaced_by`.

## Where to next

If the artifact you wrote is an agent rule, read the matching
[`agent-rules-*` collection](/collections) for the install path and the
marker contract. If it needs project-specific reference material at
render time, wire the topics through
[Knowledge buckets](/docs/knowledge-buckets). For the normative shapes
consult [RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol),
[RFC-0004](/docs/protocol/rfc-0004-adapters),
[RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2),
[RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent), and
[RFC-0008](/docs/protocol/rfc-0008-catalog-reform). For the end-to-end
command surface see the [CLI reference](/cli); for the agent launch /
detection matrix see the [agent matrix](/docs/agent-matrix).
