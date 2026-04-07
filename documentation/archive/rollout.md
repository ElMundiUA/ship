# Rollout phases

**Purpose:** reduce **big-bang** risk when adopting the pattern.  
**Audience:** engineering leadership, platform team.  
**Outcomes:** ordered phases with clear gates.

## Phase 0 — Readiness

- Confirm [Vision & extensibility](enterprise.md) fits your risk appetite.
- Align on Linear project structure and label policy ([Glossary](GLOSSARY.md)).
- Security sign-off on [Security brief](security-brief.md) questions.

## Phase 1 — Pilot (single team / project)

- Enable **SDLC scheduled** workflow for **one** pre-release project.
- Keep **daily audits** off or read-only until SDLC stable.
- Success criteria: tickets move predictably **Todo → In Progress** with auditable comments; no surprise picks from **Backlog**.

## Phase 2 — Broaden automation surface

- Add **daily audits** (tech / QA / security) with dedicated Linear projects.
- Introduce **workflow self-heal** if CI noise is a pain point ([Workflows catalog](WORKFLOWS-CATALOG.md)).

## Phase 3 — Hardening

- **E2E regression** on hosted dev tied to release checklist ([Pre-release & E2E](PRE-RELEASE-DEPLOY-E2E.md)).
- Tune cron throughput vs Cursor rate limits ([SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md)).

## Phase 4 — Optional migration experiments

- Evaluate **Cursor Automations** vs GitHub-orchestrated flow ([migration guide](CURSOR-AUTOMATIONS-MIGRATION.md)).

**Governance:** see [Governance & RACI](governance-raci.md).
