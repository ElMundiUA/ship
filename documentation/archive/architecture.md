# Architecture & data flows

Execution is anchored in **GitHub Actions** in **whatever repository runs your workflows** (this Ship package alone, or a product monorepo that vendors it). The **Cursor Cloud Agent** runs in Cursor’s cloud against a cloned repo; **Linear** receives updates via API when keys are available.

!!! note "Diagrams on this page"
    **System context** — components and trust boundaries (`architecture.svg`). **SDLC states** — Backlog vs Todo entry (`sdlc-states.svg`, also used on [Vision & extensibility](enterprise.md)). Sources: `documentation/diagrams/*.d2`; SVGs regenerate on `mkdocs build` if `d2` is on `PATH`.

## System context (D2)

Rendered from `documentation/diagrams/architecture.d2` (regenerated on build if the `d2` CLI is installed).

![System context — Linear, GitHub Actions, Cursor, human gates](diagrams/architecture.svg)

_Source:_ edit `diagrams/architecture.d2`, then run `d2 diagrams/architecture.d2 diagrams/architecture.svg` or rely on the MkDocs `hooks/d2_prebuild.py` hook.

## SDLC state emphasis (D2)

High-level view of how **Backlog** (human) vs **Todo** (automation entry) differs.

![SDLC state machine (simplified)](diagrams/sdlc-states.svg)

## Linear projects

Typical split (names are **yours** — configure via env; one concrete mapping: [Examples → Reference org](../examples/elmundi/index.md)):

| Project | Role |
|---------|------|
| **Delivery / pre-release** | Operational SDLC. Automation **does not** pick from **Backlog**; issues must be in **Todo** with pick filters (project + labels). |
| **Tech debt** | Tech architect & QA architect findings (evidence-based). |
| **Security** | Dependency/security items from scanners (deduplicated). |

Create tech/security projects once: `node scripts/ensure-audit-linear-projects.mjs` (see [Daily audits](DAILY-AUDIT-ROLES.md)).

## Responsibility boundaries

- **GitHub** — triggers, secrets, sparse checkout, `node scripts/…`.
- **Pick scripts** — deterministic selection; business rules live in Linear + prompts.
- **Cursor Cloud Agent** — code changes, optional Linear API calls, PRs per prompt contract.
- **Humans** — Backlog triage, merge, production promote, final states.

Next: operational detail in [SDLC (scheduled)](SDLC-AUTOMATION-SETUP.md).
