---
artifact_kind: workflow
id: pipeline-self-heal
name: Pipeline self-heal
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 1afe4a7ba3043dd665560db81d421f64cbe7474ead7c91ab2707b73cc6fe043b
deprecated: false
replaced_by: null
yanked: false
group: operations
tags: [diagnostics, reliability, ops]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Diagnostics cadence separate from SDLC picks; report before optional agent repair. Use when designing the corresponding lane in a Ship cron grid, when adapting CI to enforce this scheduler intent, or when reviewing automation that touches diagnostics, reliability, ops.
spec:
  intent: cron
  runtime: github-actions
  install_target: .github/workflows/pipeline-self-heal.yml
---

# Pipeline self-heal

**Intent:** detect and repair **workflow/config drift** (broken cron, stale secret names, runner starvation) **without** stealing the SDLC intake slot.

## Invariants

- Self-heal cadence is **not** the same job as intake/BA/developer—different clock or odd hours so operators can read logs without mixing stories.
- First response is **CLI/report evidence**; optional agent only when a ticket exists and policy allows.

## What you ship

- A diagnostics workflow that emits a human-readable report artifact.
- Optional follow-up that opens or updates a **tech-debt** ticket with links.

## Read next

- [GitHub Actions](/tools/github-actions).
- [Cloud agent — workflow self-heal](/patterns/cloud-workflow-self-heal) — prompt body.
- [Examples → ElMundi](/docs/examples/elmundi) — `workflow-self-heal` reference naming.
