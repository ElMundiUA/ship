---
artifact_kind: collection
id: web-application
name: Web application
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 7157e232933a98e87c7803c6485752950902b6440042a62eb30766183c7ef288
deprecated: false
replaced_by: null
yanked: false
group: product
tags: [web, e2e, spa]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  SDLC grid, PR previews, hosted Playwright, and audit lanes — picked for browser apps. Use when bootstrapping a Ship project that matches this product shape, when picking a starter set with `shipctl init`, or when the addendums or presets it composes need updating.
spec:
  subkind: starter
  install_target: documentation/collections/web-application.md
---

# Collection — Web application delivery

A **ready bundle** for teams shipping a **browser app** (SPA/SSR) with hosted previews, SDLC agents, and regression against a live dev URL.

## Workflows (enable these behaviours)

| Workflow intent | Entry |
|-----------------|-------|
| SDLC lane on a clock | [/workflows/scheduled-sdlc-lane](/workflows/scheduled-sdlc-lane) |
| PR CI + preview discipline | [/workflows/pr-and-ci-gate](/workflows/pr-and-ci-gate) |
| Hosted E2E on dev/stage | [/workflows/hosted-e2e-regression](/workflows/hosted-e2e-regression) |
| Self-heal without stealing intake | [/workflows/pipeline-self-heal](/workflows/pipeline-self-heal) |
| Parallel audits | [/workflows/parallel-audit-lanes](/workflows/parallel-audit-lanes) |

## Tools (wire these surfaces)

| Surface | Catalog |
|---------|---------|
| Linear (queue truth) | [/tools/linear](/tools/linear) |
| GitHub Actions (scheduler) | [/tools/github-actions](/tools/github-actions) |
| Playwright (hosted runner) | [/tools/playwright](/tools/playwright) |
| Cursor Cloud Agent | [/tools/cursor-cloud-agent](/tools/cursor-cloud-agent) |
| Methodology API | [/tools/methodology-api](/tools/methodology-api) |
| Tracker contract | [/tools/tracker-contract](/tools/tracker-contract) |

## Patterns (prompt bodies to fork)

| Role / slice | Entry |
|--------------|-------|
| Cloud base + guardrails | [/patterns/cloud-base](/patterns/cloud-base) |
| Developer lane | [/patterns/cloud-developer](/patterns/cloud-developer) |
| QA architect | [/patterns/cloud-qa-architect](/patterns/cloud-qa-architect) |
| Preview smoke check | [/patterns/catalog-a7-preview-validation](/patterns/catalog-a7-preview-validation) |
| Acceptance verification | [/patterns/catalog-a9-qa](/patterns/catalog-a9-qa) |
| Onboarding | [/patterns/adopt-ship-generic](/patterns/adopt-ship-generic) · [/patterns/adopt-ship-elmundi](/patterns/adopt-ship-elmundi) (reference org addendum) |

## Manual chapters

- [Getting started](/docs/getting-started) — agent prompt builder.
- [Prompts & workflows](/docs/prompts-workflows) — how prompt text evolves.
- [Examples → ElMundi](/docs/examples/elmundi) — YAML filenames, cron, secrets (reference org).
