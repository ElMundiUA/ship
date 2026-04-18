---
artifact_kind: tool
id: capabilities-overview
name: Five capabilities
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 5a078641fd1756a8351f231ce1066f96253f17087515d46ef18f7574e8b46abb
deprecated: false
replaced_by: null
yanked: false
group: platform
tags: [map, architecture]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Neutral map of what a Ship-style setup needs — no vendor lock-in in the names. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under platform needs to call into it.
spec:
  capability: platform
  install_target: documentation/tools/index.md
---

# Tools

Ship is tool-agnostic. This section defines **capability boundaries**, not mandatory vendors.

## Five capabilities every setup needs

| Capability | Why it exists | Typical implementations |
|------------|---------------|-------------------------|
| Tracker truth | Shared queue and state machine | Linear, Jira, GitHub Issues, Azure Boards, ClickUp, spreadsheet |
| Scheduler | Deterministic timing and retries | GitHub Actions, GitLab CI, Buildkite, cron + runner |
| Agent runtime | Executes prompts against repository | Cursor, Codex, Claude Code, Copilot + scripts |
| Regression runner | Verifies product integrity in hosted env | Playwright, Cypress, custom e2e |
| Security/dependency signal | Adds evidence for risk decisions | Snyk, OSS scanners, internal tooling |

## What Ship standardizes

- Interfaces between these capabilities.
- Guardrails (queue fences, state transitions, evidence trail).
- Prompt-driven adoption workflow.
- Reference implementations for real-world patterns.

## What Ship does not standardize

- Vendor lock-in.
- One mandatory API surface.
- One mandatory deployment topology.

## Use with other docs

- Setup path: [Getting started](../getting-started/index.md)
- Adaptation details: [Tracker adaptation contract](ship-agent-trackers.md)
- API surface for agents: [Backend API](backend-api.md)
- Process policy: [Delivery, quality & release](../adoption/delivery-quality-and-release-process.md)
- Why these boundaries exist: [The book](../framework/index.md)
