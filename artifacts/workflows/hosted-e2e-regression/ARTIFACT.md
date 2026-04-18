---
artifact_kind: workflow
id: hosted-e2e-regression
name: Hosted E2E regression
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: a7bb91408a4e4f40e4eb8da6b37931ad13e7577a44ca6d37cdba0a920cac4be1
deprecated: false
replaced_by: null
yanked: false
group: quality
tags: [e2e, playwright, regression]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Playwright (or similar) against dev/stage on a schedule and on demand. Use when designing the corresponding lane in a Ship cron grid, when adapting CI to enforce this scheduler intent, or when reviewing automation that touches e2e, playwright, regression.
spec:
  intent: cron
  runtime: github-actions
  install_target: .github/workflows/hosted-e2e-regression.yml
---

# Hosted E2E regression

**Intent:** prove behaviour against a **real URL** (dev/stage) on a schedule and on demand—not only pre-merge unit tests.

## Invariants

- Tests target **pinned base URLs** and known auth strategy; “works on my laptop” is not the bar.
- Failures produce **artifacts** (trace, HTML report) referenced from the ticket or PR.

## What you ship

- A workflow that runs the E2E suite against **hosted dev** (scheduled + `workflow_dispatch`).
- Separate lane from **SDLC picks** so a red suite does not masquerade as “intake failed.”

## Read next

- [Playwright](/tools/playwright).
- [Acceptance verification](/patterns/catalog-a9-qa).
- [Examples → ElMundi](/docs/examples/elmundi) — reference wiring for E2E jobs.
