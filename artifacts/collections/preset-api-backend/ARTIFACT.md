---
artifact_kind: collection
id: preset-api-backend
name: Preset — API / backend service
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-17T21:15:32.596636+00:00"
content_sha256: 3a0c2b56ed62fc788e91061f51bec185bd96751bc99d841ce6a2ec0fe056666b
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, api-backend]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for stateless HTTP/gRPC services with contract tests and migration discipline. Use when bootstrapping a Ship project that matches this preset shape, when picking a starter set with `shipctl init`, or when the addendums or presets it composes need updating.
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues]
  compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
  compatible_agents: [cursor, codex, claude, aider, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>]
  optional_tools: [tool/contract/pact, tool/contract/schemathesis, tool/migrations/atlas, tool/migrations/dbmate]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: api-backend
  install_target: documentation/collections/preset-api-backend.md
---

# Preset — API / backend service

## Product shape

Stateless HTTP or gRPC service (or a small fleet of them) —
language-agnostic. Bounded context is **"the request"**: an
idempotent, contract-bound operation with explicit error
shapes, not a user session.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state.
- Optional `Contract Frozen` checkpoint between `Todo` and
  `In Progress`: any PR that changes a public endpoint must
  lock a `contract:*` decision before work starts.

## Label contract (preset-specific)

- `contract:breaking` — schema/endpoint change requires
  consumer migration; needs versioning plan.
- `contract:additive` — safely additive, no consumer impact.
- `migration:db` — includes a database migration; needs a
  rollback plan in the PR description.
- `migration:backfill` — requires a data backfill job.
- `surface:grpc` / `surface:http` / `surface:graphql`.
- Plus the base Ship labels (`type:*`, `lane:*`, `promote:*`).

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:
  lint-typecheck:
  unit:
  contract:        # pact / schemathesis / OpenAPI diff
  migration-plan:  # dry-run migrations against a snapshot
  integration:     # ephemeral DB + docker-compose deps
  security-scan:   # sast + deps (optional but recommended)
  doctor:          # shipctl doctor
```

No browser E2E; invest the equivalent budget in contract and
integration suites instead.

## Evidence types

- OpenAPI / proto diff posted on the PR.
- Contract-test report (consumer-pact outcomes).
- Migration plan + rollback instructions in the PR body for
  any `migration:*` label.
- Integration-test artifact (replayable request log).

## Promote gates

`contract green → migrations dry-run green → staging deploy
(with migrations applied) → staging smoke → production deploy
(with explicit migration acknowledgement)`.

Any `contract:breaking` label forces an extra gate: a
versioning decision (new route, header, or deprecation
window) must be linked on the ticket before promote.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Container registry credentials (push target).
- Database URL / credentials for ephemeral CI databases.
- Cloud deploy token (AWS role, GCP SA, or equivalent) for
  staging / production jobs.

## Recommended addendums

- `addendum-pharma` — if the API handles PHI or consent data.
- `addendum-fin` — if the API drives money movement or
  regulated ledgers.

Addendums only tighten rules here — e.g. pharma mandates
audit-log retention and access-control overlays on top of the
base gates.
