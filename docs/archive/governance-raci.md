# Governance & RACI

**Purpose:** clarify **who owns policy** vs **who operates** the automation.  
**Audience:** engineering managers, platform, security.  
**Outcomes:** fewer ambiguous approvals and prompt edits.

Adjust roles to your org titles; this is a **template**.

| Activity | Product / EM | Platform / DevOps | Security | Developers |
|----------|--------------|-------------------|----------|------------|
| **Backlog prioritisation** | A / R | C | I | C |
| **Promote to Todo / labels** | A | C | I | R |
| **Prompt & skill content** | A | R | C | C |
| **GitHub secrets & envs** | I | A / R | C | I |
| **Merge / release** | A | I | I | R |
| **Audit findings triage** | A | C | R | I |
| **Snyk token & policy** | I | C | A / R | I |

**Legend:** **R** = responsible, **A** = accountable, **C** = consulted, **I** = informed.

**Related:** [Executive brief](executive-brief.md) · [Daily audits](DAILY-AUDIT-ROLES.md) · [Autonomous pipeline setup](AUTONOMOUS-SETUP.md).
