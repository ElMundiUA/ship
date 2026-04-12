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
