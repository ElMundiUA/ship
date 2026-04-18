---
artifact_kind: collection
id: api-backend
name: API / backend service
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 7f09d9a5b6ef3a39d7975f6da8b66f5b5ead7a35ecf2eaa2cd5021a86219a6ba
deprecated: false
replaced_by: null
yanked: false
group: product
tags: [api, contracts, services]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  SDLC with CI gates and security/architecture audits; lighter on browser E2E, heavier on contracts. Use when bootstrapping a Ship project that matches this product shape, when picking a starter set with `shipctl init`, or when the addendums or presets it composes need updating.
spec:
  subkind: starter
  install_target: documentation/collections/api-backend.md
---

# Collection — API / backend service

For **HTTP APIs**, workers, and services where the **browser is not the product**, but you still want SDLC discipline, CI gates, and optional contract tests.

## Workflows

| Intent | Link |
|--------|------|
| SDLC lane | [/workflows/scheduled-sdlc-lane](/workflows/scheduled-sdlc-lane) |
| PR & CI gate | [/workflows/pr-and-ci-gate](/workflows/pr-and-ci-gate) |
| Self-heal | [/workflows/pipeline-self-heal](/workflows/pipeline-self-heal) |
| Audits (security / architecture) | [/workflows/parallel-audit-lanes](/workflows/parallel-audit-lanes) |

Hosted browser E2E is **optional**; if you skip it, invest in **contract / API tests** and integration suites instead—still attach evidence to tickets.

## Tools

| Surface | Link |
|---------|------|
| Tracker + contract | [/tools/linear](/tools/linear) · [/tools/tracker-contract](/tools/tracker-contract) |
| Scheduler | [/tools/github-actions](/tools/github-actions) |
| Agents | [/tools/cursor-cloud-agent](/tools/cursor-cloud-agent) |
| Search / fetch for agents | [/tools/methodology-api](/tools/methodology-api) |

## Patterns

| Slice | Link |
|-------|------|
| Developer | [/patterns/cloud-developer](/patterns/cloud-developer) |
| Tech architect | [/patterns/cloud-tech-architect](/patterns/cloud-tech-architect) |
| Security officer | [/patterns/cloud-security-officer](/patterns/cloud-security-officer) |
| Check failure recovery | [/patterns/catalog-a6-check-failure-recovery](/patterns/catalog-a6-check-failure-recovery) |
| Onboarding | [/patterns/adopt-ship-generic](/patterns/adopt-ship-generic) |
