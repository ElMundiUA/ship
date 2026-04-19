---
artifact_kind: workflow
id: scheduled-sdlc-lane
name: Scheduled SDLC lane
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: a179eca90a4f7b69492a3dd39dcd80be0e5be1312baf2fff54b9dfad18156fa8
deprecated: false
replaced_by: null
yanked: false
group: delivery
tags: [cron, linear, github-actions]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Cron-driven intake → clarification → BA → developer with queue discipline and evidence. Use when designing the corresponding lane in a Ship cron grid, when adapting CI to enforce this scheduler intent, or when reviewing automation that touches cron, linear, github-actions.
spec:
  intent: cron
  runtime: github-actions
  install_target: .github/workflows/scheduled-sdlc-lane.yml
---

# Scheduled SDLC lane

**Intent:** run **intake → clarification → BA → developer** on a clock, **one role per slot**, with picks only from an agreed queue state (typically **Todo** in a dedicated Linear project).

## Invariants

- **Human triage** stays in **Backlog**; automation never “helps” by dragging cards out of human-only columns without policy.
- **Concurrency:** a slot should not start a second pick for the same lane while the first is still in flight—use workflow concurrency groups or equivalent.
- **Evidence:** every transition leaves a **tracker comment**, **PR URL**, or **CI run** pointer.

## What you ship in CI

- A scheduler workflow (GitHub Actions, GitLab, etc.) with **deterministic minutes** and `workflow_dispatch` for manual replay.
- Small **pick** scripts that return a single issue key—or nothing—never a vague “top of column” without filters.

## Read next

- [GitHub Actions](/tools/github-actions) — scheduler role.
- [Linear](/tools/linear) — tracker role.
- [Prompts & workflows — SDLC story](/patterns) — narrative + diagram.
- [Examples → ElMundi](/use-cases/elmundi) — reference YAML names and minutes.
