# Executive brief

**Purpose:** a **buyer-safe**, short summary of the automation model (no operator runbook detail).  
**Audience:** VP Engineering, CTO, Head of Platform, procurement preview.  
**Outcomes:** clear problem/solution boundary, trust model, suggested next step.

!!! note "Reference implementation"
    This site documents **Ship** generically and links to **[Examples → Reference org](../examples/elmundi/index.md)** for one fully wired public layout. For external collateral, replace org-specific names, URLs, and keys — or export only the pages under **Adoption** + **Vision** + **Architecture**.

## Problem

Delivery teams need **repeatable SDLC steps** (intake, clarification, analysis, implementation) without **agent stampedes**, **opaque bot behaviour**, or **unbounded work-in-progress** from AI tooling.

## Approach (governed automation)

1. **Work tracker as source of truth** — states, labels, and project membership define what automation may touch (**Backlog** stays human-only; **Todo** is the controlled entry).
2. **Scheduler, not chaos** — GitHub Actions runs **one role per time slot**; queues remain visible in the tracker.
3. **Deterministic selection** — pick scripts choose **at most one** issue per run using explicit filters.
4. **Agent contract** — prompts + branch/PR conventions (`fix/TICKET-auto`, `Closes TICKET`) keep output reviewable.
5. **Separate audit loop** — architecture / QA / security findings go to **dedicated projects** with **no-fabrication** rules so governance does not flood the sprint board.

## What stays human

- Backlog triage, merge decisions, production promote, policy for prompts and labels.
- Final accountability for security, compliance, and customer data.

## Trust & portability

- **Audit trail:** automation is tied to tickets, workflow runs, and tagged comments (see [Vision & extensibility](enterprise.md)).
- **Vendor flexibility:** orchestration is standard CI + Node; the agent provider is intended to be **swappable** behind the same behavioural contract.

## Comparison (at a glance)

| Dimension | Ad-hoc scripts | This pattern |
|-----------|----------------|--------------|
| **Audit trail** | Varies | Ticket + workflow + tagged comments |
| **Throughput control** | Often unbounded | One automated role per slot |
| **Blast radius** | Unclear | Labels + project guards |
| **Onboarding** | Tribal knowledge | Runbooks + diagrams |

## Evidence we can share (without internal URLs)

- Diagrams: [Architecture](architecture.md), state emphasis on [Vision](enterprise.md).
- **Security narrative:** [Security brief](security-brief.md).
- **Procurement questions:** [Procurement FAQ](procurement-faq.md).

## Suggested next step

- **POC scope:** one Linear project, one team, SDLC roles only; daily audits optional second phase — see [Rollout phases](rollout.md).

**Operators:** continue to [Where to find things](where-to-find.md) and [SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md).
