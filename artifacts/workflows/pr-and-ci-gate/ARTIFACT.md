---
artifact_kind: workflow
id: pr-and-ci-gate
name: "PR gate & preview"
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: fc318276707a4cdbc9d7b9ea6ca5521cf93222bb087b2d179fd53422adf6e883
deprecated: false
replaced_by: null
yanked: false
group: delivery
tags: [pr, checks, preview]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Required checks, optional preview deploy, blocking policy and markers. Use when designing the corresponding lane in a Ship cron grid, when adapting CI to enforce this scheduler intent, or when reviewing automation that touches pr, checks, preview.
spec:
  intent: cron
  runtime: github-actions
  install_target: .github/workflows/pr-and-ci-gate.yml
---

# PR gate & preview

**Intent:** every change set passes through **CI truth** (build, unit, static checks) and, when policy demands it, a **hosted preview** with evidence attached to the PR or ticket.

## Invariants

- Red checks are **blocking** unless policy explicitly allows merge with waiver—and waiver is visible in the audit trail.
- Preview URLs are **stable enough** for reviewers; flaky infra is tracked as **infra debt**, not “ignored QA.”

## What you ship

- Required status checks on the default branch.
- Optional preview deploy workflow on PR synchronize.
- Marker pattern in PR bodies/comments so agents do not spam duplicates (see your org’s marker contract).

## Read next

- [Playwright](/tools/playwright) — hosted regression runner role.
- [Preview smoke check](/patterns/catalog-a7-preview-validation) — lane prompt intent.
- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — gate language.
