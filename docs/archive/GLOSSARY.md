# Glossary

**SDLC (scheduled)** — The main delivery lane driven by `linear-agent-sdlc-scheduled.yml`: intake → clarification → BA → developer, one role per cron slot, picks only from **Todo** in the pre-release Linear project (not **Backlog**).

**Daily audits** — Separate schedule (`linear-agent-daily-audits.yml`): tech architect, QA architect, security officer. Does **not** consume the SDLC queue; writes to **tech debt** and **security** Linear projects with evidence-only rules.

**Autonomous loop** — `linear-agent-autonomous.yml`: complementary automation with its own cadence; does not replace SDLC.

**Workflow self-heal** — `workflow-self-heal.yml`: analyzes pipeline health (CLI report first), optionally launches Cloud Agent on a configured Linear issue.

**Pick scripts** — `scripts/pick-*.mjs`: deterministic selection of at most one issue per run using team, column, project, and labels.

**`cloud-agent-launch.mjs`** — Assembles prompts from `cloud-prompts/*.md` and `.cursor/skills`, calls the Cursor Cloud Agents API.

**linear-agent CLI** — `dist/cli.js` at Ship package root (built from TypeScript): `start`, `get`, `init`, `next`, `pr-create`, etc.

**Backlog vs Todo (automation)** — **Backlog** = human triage only; SDLC automation does not pick there. **Todo** = entry point for automated picks once cards are promoted and labeled per runbook.

**`ready:developer`** — Label gating the developer pick (Todo + project + this label).

**`LINEAR_*` variables** — GitHub Actions variables / secrets naming the Linear team, SDLC project, audit projects, etc. See runbooks for defaults and overrides.

**E2E** — End-to-end tests; in this repo often Playwright against hosted dev (`e2e-regression-dev.yml`).

**MCP** — Model Context Protocol; referenced in Cursor Automations for Linear tooling.

**UTC grid** — Even-hour slots for SDLC roles (:10 / :25 / :40 / :55) and related schedules; canonical detail in [SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md).
