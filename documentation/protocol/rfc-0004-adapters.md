---
rfc: 0004
title: "Adapters"
status: Accepted
created: 2026-04-17
---

# RFC-0004 — Adapters

## Summary

Adapters are how Ship grows to cover new CI systems, trackers, agents, and rule bundles without shipping a new CLI release. Each adapter is a versioned artifact under one of these categories:

- `tools/ci/<name>` — CI systems (`gh-actions`, `gitlab-ci`, etc.).
- `tools/tracker/<name>` — issue trackers (`linear`, `jira`, etc.).
- `tools/agent/<name>` — agent runtimes (`cursor`, `codex`, etc.).
- `collections/agent-rules/<name>` — curated rule bundles per agent / stack.

Adapters are not compiled into `shipctl`. They are fetched at runtime via the artifacts protocol (RFC-0001) and interpreted by the CLI. Adding support for a new tracker, CI, or agent is a content change on the Ship site: publish a new artifact, done. No CLI release required.

## Why adapters, not plugins

- A plugin system means binaries and trust. Adapters are markdown with well-defined sections, interpreted by the CLI.
- Plugins couple release cadence to CLI releases. Adapters track the Ship site cadence.
- Plugins explode the test surface. Adapters share a single interpreter and a single test harness (open question below).

## Adapter API (conceptual)

For CLI implementors, every adapter exposes three hooks. These are not code: they are sections inside the artifact that `shipctl` reads and executes deterministically.

### `detect(cwd)`

Runs when `shipctl init` scans the repository.

```
detect(cwd) → { present: bool, confidence: 0..1, evidence: [...] }
```

- `present` — whether the adapter applies at all (e.g. `gh-actions` sees `.github/workflows/`).
- `confidence` — `0..1`. Used to break ties between competing adapters (`circleci` vs `gh-actions` in a migrating repo).
- `evidence` — human-readable strings (paths or small facts) shown in `shipctl doctor` output.

### `bootstrap(cfg)`

Runs during `shipctl init --bootstrap` once an adapter has been selected (by detection or by `stack.*` in `.ship/config.yml`).

```
bootstrap(cfg) → { files_written: [...], secrets_required: [...], post_steps: [...] }
```

- `files_written` — files the adapter created or updated, with absolute paths.
- `secrets_required` — secret names (never values) that must be present in the adopter's secret store.
- `post_steps` — short, numbered actions the user must still perform manually (e.g. "Enable the Linear webhook on https://…").

### `verify(cfg)`

Runs on `shipctl doctor`.

```
verify(cfg) → { ok: bool, checks: [{name, status, detail}] }
```

Each check reports `status: "pass" | "warn" | "fail"` and a short `detail` string. A `fail` check drops `ok` to `false`.

## Template rendering

An adapter artifact is a single markdown file with strict structure. Front-matter identifies the adapter:

```
---
kind: adapter
subkind: tracker|ci|agent|rules
name: linear
---
```

The body uses named sections. `shipctl` is strict about section headings and will refuse to load an adapter that is missing a required section.

### Required sections

| Section                                | Purpose                                                                                        |
|----------------------------------------|------------------------------------------------------------------------------------------------|
| `## Detect (signals)`                  | Bullet list of detection signals (file globs, env vars, binaries on PATH).                     |
| `## Bootstrap (files to write)`        | One or more fenced code blocks, each tagged with `path="<target>"`. The block body is the file. |
| `## Verify (live checks)`              | Bullet list of checks, each with a name and a command or filesystem predicate.                 |
| `## Secrets`                           | Bullet list of secret names (uppercased), one per line, each with a one-line description.      |
| `## Variables`                         | Bullet list of template variables the adapter consumes (for example `ORG_SLUG`, `DEFAULT_BRANCH`). |

### Example `## Bootstrap` block

```markdown
## Bootstrap (files to write)

Write the GitHub Actions workflow for PR CI:

```path=".github/workflows/ship-ci.yml"
name: Ship CI
on:
  pull_request:
    branches: [main]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: npm ci
      - run: npm test
```
```

`shipctl` extracts the `path="..."` attribute and writes the block body to that path, refusing to overwrite an existing file unless `--force` is passed.

### Conventions

- All variables use `{{ NAME }}` interpolation. Undefined variables abort bootstrap with a clear error.
- Conditional blocks `{{#if VAR}}…{{/if}}` are supported with the same variable resolution rules; the inner block is emitted iff `VAR` is truthy after substitution.
- Loop syntax (`{{#each …}}…{{/each}}`) is **deferred**: not available in v1. An adapter that needs to fan out over a list must split into multiple bootstrap blocks or use `requires` to compose a dedicated child adapter. We may revisit loops once usage data shows the gap is real.
- No shell execution inside `## Bootstrap`. File writes only.
- `## Verify` may reference commands, but `shipctl` runs them with a whitelist of safe subcommands (e.g. `git rev-parse`, `gh auth status`, `curl -I`).
- Markdown outside these sections is allowed as documentation for humans; `shipctl` ignores it.

### Append-safe merges via `## Patch`

When two adapters need to write into the same file (typical case: a CI
adapter writes `.github/workflows/ship-ci.yml`; an agent adapter wants to
add a job referencing the agent's secrets), the secondary adapter declares
a `## Patch` section instead of a fresh `## Bootstrap` block.

A `## Patch` block looks like:

````markdown
## Patch

```path=".github/workflows/ship-ci.yml" mode="append" marker="ship-managed:agent-cursor"
  agent_cursor_smoke:
    runs-on: ubuntu-latest
    needs: [test]
    steps:
      - uses: actions/checkout@v4
      - run: npx -y @elmundi/ship-cli verify --no-network
```
````

Rules:

- `path="..."` — required; must already exist (created by another adapter or a `## Bootstrap` step earlier in the same run).
- `mode="append"` — append to the end of the file. `mode="replace-block"` is also supported and replaces an existing block delimited by the same `marker`.
- `marker="ship-managed:<id>"` — required. `shipctl` wraps the block on disk with markers
  ```
  # --- ship-managed:<id> begin ---
  …block body…
  # --- ship-managed:<id> end ---
  ```
  using the comment style appropriate to the file extension (the same convention that `init --bootstrap` uses for `.env.example` today).
- Re-runs are idempotent: a block whose marker already exists is replaced in place.
- Only one adapter may own a given marker id; conflicts are a fail-fast error and prompt the user to pick distinct ids.
- A `## Patch` block never overrides another adapter's `## Bootstrap` write — it adds inside the same file with a clearly attributed boundary so reviewers can tell who wrote what.

## Supported matrix (MVP)

This is the MVP set. Adding to these lists does not require a schema change; it requires publishing a new adapter artifact.

### CI

| Name             | Notes                                                   |
|------------------|---------------------------------------------------------|
| `gh-actions`     | Default for most public repos.                          |
| `gitlab-ci`      | `.gitlab-ci.yml` at root.                               |
| `buildkite`      | `.buildkite/pipeline.yml`.                              |
| `circleci`       | `.circleci/config.yml`.                                 |
| `azure-pipelines`| `azure-pipelines.yml` at root.                          |
| `jenkins`        | `Jenkinsfile`.                                          |
| `manual`         | No CI adapter; verify step reminds about local checks.  |

### Tracker

| Name             | Notes                                                                      |
|------------------|----------------------------------------------------------------------------|
| `linear`         | Canonical tracker for reference org. Uses API key + workspace slug.        |
| `jira`           | Cloud and on-prem variants; adapter asks which during bootstrap.           |
| `github-issues`  | Repo-scoped; labels map to lanes.                                          |
| `azure-boards`   | Azure DevOps organizations.                                                |
| `clickup`        | Workspace + list ids.                                                      |
| `spreadsheet`    | Fallback for small teams. Google Sheets / Excel template shipped by adapter. |
| `none`           | Tracker-less teams; queues live in PR labels only.                         |

### Agents

| Name        | Notes                                                                |
|-------------|----------------------------------------------------------------------|
| `cursor`    | IDE; rules live in `.cursor/rules/` and `AGENTS.md`.                 |
| `codex`     | CLI + cloud agent.                                                   |
| `claude`    | Claude Code, local + SaaS.                                           |
| `aider`     | CLI pair-programmer.                                                 |
| `copilot`   | GitHub Copilot.                                                      |
| `cline`     | VS Code extension agent.                                             |
| `continue`  | IDE agent extension.                                                 |
| `windsurf`  | Codeium IDE.                                                         |
| `zed`       | Zed editor agents.                                                   |
| `gemini`    | Gemini Code Assist.                                                  |
| `opencode`  | Open-source CLI agent.                                               |

### Presets

| Name                 | Purpose                                                                               |
|----------------------|---------------------------------------------------------------------------------------|
| `web-app`            | Browser-first product. Next.js / Vite / SPA.                                          |
| `api-backend`        | Service repo with HTTP/gRPC surface. Language-agnostic.                               |
| `mobile-app`         | iOS / Android / cross-platform client.                                                |
| `cli`                | Developer CLI or SDK repo.                                                            |
| `monorepo`           | Multi-package root; expects per-package bootstrap later.                              |
| `adoption-minimum`   | Minimum viable adoption — documentation and queue lanes only, no new CI.              |

## Addendums

Some verticals need extra guardrails that are not universal. Adapters and presets can declare an `addendum` — itself an artifact — that layers on additional rules and CI checks.

Examples:

- `collections/addendum-pharma@1.0.0` — PHI redaction reminders in agent rules; audit-log retention note in CI template; stricter PR evidence checklist.
- `collections/addendum-fin@1.0.0` — change-management comments on merges; artifact pin policy; reviewer-role requirement.
- `collections/addendum-health@1.0.0` — HIPAA-flavored rules, de-identification reminders, evidence retention.

Addendums are not automatically activated. They are declared on a preset:

```yaml
stack:
  preset: "api-backend"
artifacts:
  pins:
    collection/addendum-pharma: "1.0.0"
```

Or enabled interactively via `shipctl init --addendum pharma`. `shipctl bootstrap` applies the addendum after the base preset, and its `## Bootstrap` blocks may append sections to files the base preset already wrote.

Addendums never silently relax a rule: if a base adapter enforces X, an addendum may only tighten or annotate X, never remove it.

## Version coupling

Each adapter declares two compatibility fields inside its front-matter, plus an optional `requires` list for cross-adapter dependencies:

```
---
kind: adapter
subkind: tracker
name: linear
version: 2.3.0
min_shipctl: "0.3.0"
compatible_presets: ["web-app", "api-backend", "cli", "monorepo"]
requires:
  - "tool/ci/gh-actions@>=1.2.0"
  - "tool/agent/cursor@>=1.0.0"
---
```

Rules:

- `min_shipctl` — minimum `shipctl` semver; below this, `shipctl` refuses the adapter with a readable error.
- `compatible_presets` — list of presets this adapter supports. `shipctl init --bootstrap` refuses to apply an adapter whose list does not include the active preset.
- `requires` — optional list of cross-adapter dependencies, each of the form `<kind>/<subkind>/<name>@<range>` (semver range per RFC-0001). `shipctl init --bootstrap` resolves and applies them in topological order; a missing or out-of-range `requires` entry is a fail-fast error before any file is written. Use `requires` (instead of duplicating bootstrap blocks) whenever an adapter expects another adapter's output to already be on disk.
- Incompatible combinations fail fast with a message like:
  ```
  tracker:linear@2.3.0 is not compatible with preset 'mobile-app'.
  compatible presets: web-app, api-backend, cli, monorepo.
  pick a different preset or pin an older tracker adapter that lists mobile-app.
  ```

Adapters are also free to declare `compatible_languages: [...]` when relevant (most do not, because Ship is stack-agnostic on language).

## Adapter lifecycle

1. Author writes the adapter markdown.
2. CI on the Ship repo lints front-matter and required sections, computes `content_sha256`, and assigns the next version per RFC-0001 bump rules.
3. The adapter is published on `stable` or `edge` channel.
4. Adopters pick it up via `shipctl sync` and use it via `shipctl init --bootstrap` or `shipctl doctor`.
5. Deprecation uses the standard `deprecated=true / replaced_by` path (RFC-0001).

## Security

- Adapter bodies are treated as content, not code. `shipctl` never `eval`s anything from an adapter; it interprets the structured sections.
- File writes during bootstrap are sandboxed to the repo root. Writes outside the repo root are rejected.
- `## Verify` commands are whitelisted; arbitrary shell is not permitted.
- `## Secrets` only enumerates names. No secret values ever appear in adapter markdown.

## Full example: `linear` tracker adapter

Front-matter:

```
---
kind: adapter
subkind: tracker
name: linear
version: 2.3.0
min_shipctl: "0.3.0"
compatible_presets: ["web-app", "api-backend", "cli", "monorepo", "mobile-app"]
---
```

Body:

```markdown
# Linear tracker adapter

## Detect (signals)

- env var `LINEAR_API_KEY` present
- `.linear/` directory at repo root
- GitHub Action referencing `linear-app/` in any `.github/workflows/*.yml`
- Team confirms "linear" at `shipctl init`

## Bootstrap (files to write)

Write a Linear workspace hint so future `shipctl doctor` runs can verify the tracker:

```path=".ship/trackers/linear.yml"
version: 1
workspace: "{{ LINEAR_WORKSPACE_SLUG }}"
default_team: "{{ LINEAR_DEFAULT_TEAM }}"
labels:
  - intake
  - spec
  - implementation
  - review
  - release
```

Write a PR template that references Ship lanes:

```path=".github/PULL_REQUEST_TEMPLATE.md"
## Ship artifacts consumed

- pattern:role-developer@{{ ROLE_DEVELOPER_VERSION }}

## Tracker

- Linear workspace: {{ LINEAR_WORKSPACE_SLUG }}
- Ticket: <paste Linear issue URL>
```

## Verify (live checks)

- `curl -I https://api.linear.app` returns 2xx or 3xx.
- `LINEAR_API_KEY` is present in the environment (local) or in the CI secret store.
- `.ship/trackers/linear.yml` parses as valid YAML.

## Secrets

- `LINEAR_API_KEY` — personal or service-account API key from Linear.

## Variables

- `LINEAR_WORKSPACE_SLUG` — Linear workspace slug (required).
- `LINEAR_DEFAULT_TEAM` — default team name for newly-created issues (required).
- `ROLE_DEVELOPER_VERSION` — resolved version of `pattern:role-developer` (auto-filled).
```

## Full example: `cursor` agent adapter

Front-matter:

```
---
kind: adapter
subkind: agent
name: cursor
version: 1.1.0
min_shipctl: "0.3.0"
compatible_presets: ["web-app", "api-backend", "cli", "monorepo", "mobile-app", "adoption-minimum"]
---
```

Body:

```markdown
# Cursor agent adapter

## Detect (signals)

- `.cursor/` directory at repo root
- `AGENTS.md` at repo root mentions Cursor
- user lists `cursor` under `stack.agents` in `.ship/config.yml`

## Bootstrap (files to write)

```path=".cursor/rules/ship-lanes.mdc"
---
description: "Ship lane reminders for Cursor"
alwaysApply: true
---

- Consume Ship artifacts via `shipctl <kind> show <id>`; never paste bodies into the repo.
- Record `kind:id@version` for every artifact used in the PR description.
- Draft feedback via `shipctl feedback draft` when an artifact has gaps; do not auto-submit.
```

```path="AGENTS.md"
# AGENTS

## Cursor

This repository follows Ship. Consume artifacts via `shipctl`, record their
`kind:id@version` in PR descriptions, and never vendor methodology bodies
into the repository.
```

## Verify (live checks)

- `.cursor/rules/ship-lanes.mdc` exists and includes the "Ship lane reminders" header.
- `AGENTS.md` exists and references `shipctl`.

## Secrets

- none

## Variables

- none
```

## Full example: CI adapter for `gh-actions`

Front-matter:

```
---
kind: adapter
subkind: ci
name: gh-actions
version: 1.2.0
min_shipctl: "0.3.0"
compatible_presets: ["web-app", "api-backend", "cli", "monorepo", "mobile-app", "adoption-minimum"]
---
```

Body (excerpt):

```markdown
## Bootstrap (files to write)

```path=".github/workflows/ship-doctor.yml"
name: Ship doctor
on:
  pull_request:
  push:
    branches: [main]

jobs:
  doctor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npx -y @ship/shipctl doctor
```

## Verify (live checks)

- `.github/workflows/ship-doctor.yml` exists and references `shipctl doctor`.
- `gh auth status` reports a logged-in user when run locally (informational).

## Secrets

- `SHIP_API_TOKEN` — only required for private Ship mirrors.
```

## Rules collections

Beyond per-agent adapters, Ship publishes curated rule bundles as `collections/agent-rules/<name>`. These are themselves artifacts; adapters may reference them to compose a richer rules set. Example:

- `collections/agent-rules/web-app@3.0.0` — rules that make sense for a web-app preset regardless of which agent runs them.
- `collections/agent-rules/api-backend@2.1.0` — rules for API backends.
- `collections/agent-rules/monorepo@1.0.0` — rules for monorepos with per-package ownership.

A typical `stack` block referencing these:

```yaml
stack:
  preset: "web-app"
  agents: ["cursor", "codex"]
artifacts:
  pins:
    collection/agent-rules/web-app: "3.0.0"
```

`shipctl init --bootstrap` will pull the referenced collection and hand its rule bodies to the agent adapters so that Cursor and Codex both receive the same web-app rules.

## Compatibility matrix

`shipctl` maintains a runtime compatibility matrix assembled from each adapter's `compatible_presets`. Concrete examples:

| Adapter (`id@version`)                 | Compatible presets                                                     |
|----------------------------------------|-----------------------------------------------------------------------|
| `tool/ci/gh-actions@1.2.0`             | web-app, api-backend, cli, monorepo, mobile-app, adoption-minimum      |
| `tool/ci/azure-pipelines@1.0.0`        | api-backend, cli, monorepo                                             |
| `tool/tracker/spreadsheet@1.0.0`       | adoption-minimum                                                       |
| `tool/agent/cursor@1.1.0`              | all                                                                    |
| `collection/addendum-pharma@1.0.0`     | api-backend, cli, monorepo                                             |

A combination like `preset=mobile-app` + `ci=azure-pipelines` fails at `init --bootstrap` with a message listing both the adapter's compatible presets and alternative adapters that support mobile-app.

## Open questions

- **Adapter signing.** Should adapters be individually signed so that offline bootstraps can verify provenance without trusting TLS alone?
- **Adapter test harness.** Do we ship a `shipctl adapter test <file>` that runs detect / bootstrap (in a temp dir) / verify against a set of fixture repos? Today adapters rely on manual review.
- **Adapter authoring docs.** A dedicated authoring guide under `documentation/` with examples would lower the contribution bar. What is the minimum viable set of examples — one tracker, one CI, one agent, one addendum?
- **Presets-as-adapters.** Should presets themselves be adapters (`tools/preset/<name>`), or stay as the separate first-class `collection` artifact they are today?
- **Templating loops (deferred).** `{{ NAME }}` and `{{#if VAR}}…{{/if}}` are baseline; loop syntax (`{{#each …}}…{{/each}}`) is intentionally out of v1. We may revisit once we see adapters that contort around the limitation.

## Changelog

- 2026-04-17: Initial draft.
- 2026-04-17: Added optional `requires: [...]` adapter front-matter for cross-adapter dependencies (resolved in topological order, range syntax per RFC-0001); locked the templating scope to `{{ VAR }}` + `{{#if X}}…{{/if}}` for v1 with loops marked Deferred; specified append-safe merges via `## Patch` blocks with `ship-managed:<id>` markers and `mode="append" | "replace-block"`.
