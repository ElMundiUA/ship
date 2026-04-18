---
artifact_kind: collection
id: preset-adoption-minimum
name: Preset — Adoption minimum
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-17T21:15:32.596888+00:00"
content_sha256: 5fc43279d103318d4d0b5df8d81facacbfabe2556210bf2e3aafaea36c7e634b
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, adoption-minimum]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Smallest viable adoption: queue lanes and agent rules before any CI or product-specific gates. Use when bootstrapping a Ship project that matches this preset shape, when picking a starter set with `shipctl init`, or when the addendums or presets it composes need updating.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues, spreadsheet, none]
  compatible_ci: [gh-actions, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot, cline, continue, windsurf, zed, gemini, opencode]
  required_tools: [tool/tracker/<current>, collection/agent-rules-<agent>]
  optional_tools: [tool/ci/gh-actions, tool/capabilities-overview]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: adoption-minimum
  install_target: documentation/collections/preset-adoption-minimum.md
---

# Preset — Adoption minimum

## Product shape

Intentionally shape-agnostic. Use this preset when the team
wants the Ship queue discipline and agent rules **before**
committing to a CI pipeline or a product-specific preset.
Bounded context is **"the ticket"** — a unit of work with
intake, owner, evidence, and a close condition.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state.
- No optional columns at this stage — keep the grid boring
  until it clearly needs more.

## Label contract (preset-specific)

- `type:feature` / `type:fix` / `type:chore` / `type:spike`.
- `lane:delivery` / `lane:audit` / `lane:onboarding`.
- `promote:manual` — change is released by hand at this
  stage (no CI gate yet).
- Plus the base Ship labels.

## CI stages (pseudocode)

```
on: pull_request (optional)
jobs:
  doctor:          # shipctl doctor — the single required gate
```

No build, no test matrix, no release job yet; the preset
deliberately keeps CI narrow so the team can adopt Ship
without introducing new infra.

## Evidence types

- Ticket body with intake, owner, and close condition.
- `<kind>:<id>@<version>` references for every Ship artifact
  applied (even at this stage).
- PR description linking the ticket and listing manual
  verification steps taken.

## Promote gates

`ticket accepted → change applied → manual verification
recorded on ticket → merged`.

Promote gates exist as documentation (who signed off, on
what) rather than automation; CI will arrive with the next
preset the team promotes to.

## Required secrets (generic names)

- Tracker API key (or none if tracker is `spreadsheet` /
  `none`).
- Everything else is optional at this stage.

## Recommended addendums

- None at this stage. Adopt an addendum once the team moves
  to a product-specific preset (`web-app`, `api-backend`,
  `mobile-app`).
