---
artifact_kind: collection
subkind: preset
preset_id: monorepo
compatible_trackers: [linear, jira, github-issues]
compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
compatible_agents: [cursor, codex, claude, aider, copilot]
required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>]
optional_tools: [tool/monorepo/turborepo, tool/monorepo/nx, tool/monorepo/bazel, tool/release/changesets]
addendums: []   # preset itself declares no addendum; user opts in separately
min_shipctl: "0.3.0"
---

# Preset — Monorepo

## Product shape

Multi-package repository with a workspace tool
(Turborepo / Nx / pnpm workspaces / Bazel / Go workspaces)
managing many deliverables — a mix of apps, libraries, and
CLIs inside one git root. Bounded context is **"the changed
set"**: CI only runs work for packages affected by the diff.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state.
- Optional `Cross-Package Review` checkpoint between `In
  Progress` and `In Review` when a change crosses package
  boundaries — triggers a review from the downstream
  package's CODEOWNERS.

## Label contract (preset-specific)

- `package:<name>` — one per touched workspace package.
- `cross-package` — change spans more than one package.
- `release:<package>:<bump>` — planned version bump
  (`patch`/`minor`/`major`) for the package at merge time.
- `docs-only` — no publishable changes; skip release plumbing.
- Plus the base Ship labels.

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:
  affected:        # compute changed-packages graph
  lint-typecheck:  # scoped to affected + their dependents
  unit:            # scoped to affected + their dependents
  build:           # scoped to affected + their dependents
  integration:     # workspace-wide when cross-package
  release-plan:    # changesets / semantic-release dry-run
  doctor:
on: push main
jobs:
  release:         # publish per package per release:*:* label
```

## Evidence types

- Affected-package report for every PR (list + reason).
- Per-package release-plan diff (old → new version).
- CODEOWNERS approvals for every `cross-package` label.
- Aggregate workspace lint/unit/build summary.

## Promote gates

`affected pipeline green → CODEOWNERS approvals collected →
release-plan accepted → main merge → per-package publish`.

Cross-package changes cannot merge without downstream
CODEOWNERS sign-off; this is not negotiable at the preset
level.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user with write scope to every
  publishable registry.
- Per-registry publish tokens (npm, PyPI, Docker Hub / ECR /
  GCR) as appropriate.
- Monorepo remote-cache credentials (Turborepo / Nx Cloud /
  Bazel remote cache), if used.

## Recommended addendums

- `addendum-pharma` — apply scoped to any package that
  handles PHI; package-level pinning is how you opt in per
  package without forcing every workspace to adopt it.
- `addendum-fin` — same pattern, scoped to packages that
  touch money.

Addendums layer on top of the preset per-package and never
silently remove the base preset's cross-package gates.
