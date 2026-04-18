---
artifact_kind: workflow
id: parallel-audit-lanes
name: Parallel audit lanes
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: a8b8344213d6f801c748b788f828df0c202c56a15d94ec6866878d07f759b0ac
deprecated: false
replaced_by: null
yanked: false
group: governance
tags: [audit, security, governance]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Tech, QA, and security audits on separate boards — delivery throughput stays intact. Use when designing the corresponding lane in a Ship cron grid, when adapting CI to enforce this scheduler intent, or when reviewing automation that touches audit, security, governance.
spec:
  intent: cron
  runtime: github-actions
  install_target: .github/workflows/parallel-audit-lanes.yml
---

# Parallel audit lanes

**Intent:** tech / QA / security **audits** run on their own boards or projects so they never consume the **delivery Todo** queue.

## Invariants

- Audit findings link evidence (logs, scans, diffs); **no fabrication** rules when the signal is thin.
- Quiet mornings are a valid outcome—**silence is not failure** if the job truly found nothing to report.

## What you ship

- Separate Linear projects (or equivalent) and schedules distinct from SDLC.
- Prompt files for architect/security roles under `prompts/cloud-agent/`.

## Read next

- [Linear](/tools/linear) — multi-project hygiene.
- [Cloud agent — QA architect](/patterns/cloud-qa-architect) · [Security officer](/patterns/cloud-security-officer).
- [Examples → ElMundi](/docs/examples/elmundi) — daily audits chapter.
