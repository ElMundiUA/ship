---
artifact_kind: tool
id: playwright
name: Playwright
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: bc03b159c28bd506c9d1ad04b390f5854e2a317c5ad04f24c5809aa48fc71f45
deprecated: false
replaced_by: null
yanked: false
group: e2e
tags: [browser, artifacts, flake]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Hosted regression — pinned URLs, artifacts, flake vs defect triage. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under e2e needs to call into it.
spec:
  capability: e2e
  install_target: documentation/tools/integrations/playwright.md
---

# Playwright (regression runner)

**Role in Ship:** the **regression runner** capability — prove behaviour against a **hosted** URL (dev/stage), not only localhost mocks.

## What “good” looks like

- Tests run in CI (scheduled and/or on PR) with a **pinned base URL** and stable auth strategy (test users, storage state, or org-approved shortcuts).
- Failures produce **actionable artifacts** (trace, screenshot, HTML report) attached or linked from the tracker comment — not only “e2e red”.
- Flake policy: distinguish **product defect**, **environment drift**, and **test debt** (see catalog prompts for preview/E2E lanes).

## How Ship talks about it

- Hosted E2E is a **first-class gate** in delivery-quality docs: automated tests should track **accepted** behaviour.
- Prompt catalog includes preview validation and QA patterns; agents should read those before inventing new check shapes.

## Read next

- [Delivery, quality & release](/docs/operating) — where automated regression evidence sits in the gate story.
- [Prompts & workflows — catalog](/patterns) — search for preview/E2E intent sections.
- [Examples → ElMundi](/use-cases/elmundi) — reference wiring for scheduled regression vs PR preview (workflow names differ per org).
