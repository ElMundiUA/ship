---
artifact_kind: tool
id: github-actions
name: GitHub Actions
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 98f194a71a48ec702fa81ff2c3d443cd8e99fa13b82efef32352585f23d032a7
deprecated: false
replaced_by: null
yanked: false
group: ci
tags: [yaml, scheduler, secrets]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Cron, concurrency, workflow_dispatch, artifacts as the audit trail. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under ci needs to call into it.
spec:
  capability: ci
  install_target: documentation/tools/integrations/github-actions.md
---

# GitHub Actions (scheduler)

**Role in Ship:** the **scheduler** — deterministic clock, retries, concurrency, and `workflow_dispatch` for humans when the board is messy.

## What you wire

- **Cron** — one serious job per slot where possible; separate **SDLC** cadence from **self-heal** / diagnostics so failures are interpretable.
- **Secrets & variables** — GitHub holds credentials; mirror only what agents need into approved runtime envs (never paste keys into prompts).
- **Artifacts** — logs and reports from each run are part of the **evidence trail** alongside tracker comments and PRs.

## Agent touchpoints

- Workflows call small **Node entrypoints** (pick, launch, verify) that stay boring and testable; heavy reasoning stays in prompts executed by the agent runtime.
- Keep **idempotent** picks: the same slot should not fight itself (concurrency groups, single-flight labels).

## Read next

- [Tools — capability map](/docs/tools) — why scheduler is one of five capabilities.
- [Delivery, quality & release](/docs/operating) — release policy vs schedule.
- [Examples → ElMundi](/use-cases/elmundi) — sample workflow filenames and minutes (reference only).
