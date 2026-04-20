---
artifact_kind: pattern
id: kickoff
name: CI kickoff preamble
version: 1.0.0
channel: stable
min_shipctl: 0.11.2
updated_at: "2026-04-20T12:00:00+00:00"
content_sha256:57912dc49ea1a23fb3d5a0b000aaaff8b13085242a74d32b4053ae326564b40c
deprecated: false
replaced_by: null
yanked: false
group: ship-runtime
tags: [kickoff, ci, agent]
authors: ["@elmundi/ship-core"]
license: Apache-2.0
description: >-
  Short preamble injected before the workload-specific pattern in GitHub Actions.
  Fetch with `shipctl kickoff` and pipe into your agent (Claude Code, Codex, Cursor Cloud, …).
  The tracker remains the source of truth for tickets and clarifications; use `shipctl callback`
  at the end of the job for Ship observability.
spec:
  template: true
---

## Ship CI kickoff

You are executing a **Ship pipeline step inside the customer’s GitHub Actions runner**.

### Methodology

- **Patterns, tools, workflows, and collections** are versioned in Ship. Discover them with `shipctl pattern|tool|workflow|collection …` and `shipctl search <query>` against the methodology API (`api.base_url` in `.ship/config.yml`).
- **Do not assume** a fixed prompt was embedded in the workflow YAML. Fetch the workload pattern your lane needs (for example `catalog-a1-intake`, `cloud-tech-architect`) via `shipctl pattern fetch <id>` or the pins in `.ship/config.yml`.
- **Tracker is authoritative** for tickets, state transitions, and human-visible clarifications. If you need input from a human, add a comment that includes `@ship clarification:` and apply the label `ship:needs-clarification` on the ticket. Do not open a parallel clarification channel only in Ship.
- **When the lane finishes**, the workflow must call `shipctl callback` with `--status ok|fail` so the Ship dashboard can reconcile the `PipelineRun`. Use the `SHIP_RUN_TOKEN` and `SHIP_CALLBACK_URL` environment variables supplied by the dispatch inputs.

### Config hints

- `.ship/config.yml` may set `stack.agent.provider` (for example `claude-code`, `cursor-cloud`, `codex`) so operators know which CLI or cloud agent should consume this prompt. It does not change how `shipctl` runs in CI—you still invoke your agent explicitly in the workflow.

### Output discipline

- Prefer small, auditable steps: comment in the tracker, link PRs/issues, and keep summaries short enough for the callback `--summary` flag (≤1024 characters on the server).
