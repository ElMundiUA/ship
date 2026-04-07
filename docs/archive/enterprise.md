# Vision, business objectives & enterprise positioning

## Executive summary

- **Governed control loop:** the work tracker holds intent and guardrails; automation runs **scheduled, idempotent** roles with **deterministic** ticket selection.
- **Throughput without stampedes:** one automated role per time slot; queues stay visible in Linear (and via snapshot tooling).
- **Auditability:** automated touches map to tickets, workflow runs, and tagged comments.
- **Portability:** orchestration is **GitHub Actions + Node**; tracker and agent integrations aim to be **adapter-shaped** (see extensibility sections below).

This automation stack is designed as a **finite, auditable control loop** around product delivery: humans set intent and guardrails in the work tracker; machines execute repeatable roles on a schedule with **idempotent** prompts and **deterministic** ticket selection.

**Buyer-facing short read:** [Executive brief](executive-brief.md).

---

## Business objectives

| Objective | How this design supports it |
|-----------|----------------------------|
| **Predictable throughput** | One role per cron slot avoids agent stampedes; queues are visible in Linear and via `agent-queue-snapshot.mjs`. |
| **Audit trail** | Every automated touch is tied to a ticket, a workflow run, and tagged comments (`[GitHub SDLC:…]` / `[GitHub SDLC daily-audit:…]`). |
| **Quality gates** | Developer prompt mandates lint, typecheck, tests, build, smoke E2E before PR; security role consumes Snyk JSON. |
| **Operational clarity** | Backlog stays human-owned; **Todo** + project filters define what automation may touch — no “surprise” picks. |
| **Vendor flexibility** | Orchestration is plain GitHub Actions + Node scripts; the agent provider (Cursor today) is **swappable** behind the same launch contract. |

---

## Finite-state view (why it scales)

The SDLC lane behaves like a **governed state machine** (explicit states, transitions, and guards):

- **States** are Linear columns (`Backlog`, `Todo`, `In Progress`, …).
- **Events** are human moves (e.g. “promote to Todo”) or automation edges (`pick-*` → Cloud Agent → labels / PR).
- **Guards** are labels and project membership (`ready:developer`, `stage:intake`, pre-release project, etc.).

That mental model matters for compliance conversations: you can point to **explicit transition rules** in pick scripts and prompts instead of ad-hoc bot behaviour.

![Simplified SDLC state emphasis](diagrams/sdlc-states.svg)

---

## Extensibility: swap Linear for Jira (or another tracker)

The coupling surface is intentionally small:

1. **Pick scripts** (`scripts/pick-*.mjs`) — today they call Linear GraphQL with `IssueFilter`. A Jira-backed implementation would query JQL and return the same **issue key string** for the orchestrator.
2. **`cloud-agent-launch.mjs`** — resolves metadata via `dist/cli.js get`; replace with `jira get ISSUE-123` or a thin adapter.
3. **Prompts** — mention “Linear” in copy; swap to “Jira” and field semantics (priority, components) while keeping **role semantics** (intake, BA, dev).
4. **Secrets** — `LINEAR_API_KEY` → `JIRA_API_TOKEN` + base URL; GitHub vars for project/board IDs instead of `LINEAR_SDLC_PROJECT_ID`.

GitHub Actions **stay** the scheduler; only the **issue provider** and **CLI** change. You can even run **both** trackers during migration by duplicating workflows with different env prefixes.

---

## Extensibility: other agent vendors or self-hosted runners

`cloud-agent-launch.mjs` is an HTTP client to **Cursor’s Agents API**. The same orchestration pattern maps to:

- Another managed agent API (if it accepts repo + prompt + branch naming).
- **Self-hosted** job runners executing Codex / local LLM tooling — as long as they honour the contract: checkout, branch naming (`fix/ELM-XX-auto`), PR body with `Closes ELM-XX`, and Linear/Jira updates.

The **prompt bundle** (`cloud-prompts/*.md` + `.cursor/skills`) remains the portable asset.

---

## Extensibility: orchestration beyond GitHub Actions

Triggers are “run Node, then maybe launch agent”. The same scripts run locally or in:

- **GitLab CI / CircleCI / Buildkite** — schedule + secrets + checkout.
- **Argo / Airflow** — if you need cross-repo fan-out (e.g. nightly audits across many products).

Keep pick/launch as **plain Node** for lowest integration cost.

---

## Daily audits as a second, non-SDLC loop

Tech architect, QA architect, and security officer roles **do not consume** the pre-release SDLC queue. They write to **separate Linear projects** with strict “no fabrication” rules — suitable for **risk registers** and **architecture review boards** without polluting the delivery sprint board.

---

## Summary

- **Enterprise-grade control model** = explicit states, guards, audit markers, and swappable adapters — not a single-vendor “magic” integration.
- **Current stack** = Linear + GitHub Actions + Cursor Cloud Agent + Snyk — documented as **one implementation** of that pattern.

Next: [Architecture](architecture.md) for system context, then [SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md) for day-to-day operations.
