---
slug: 2026-04-26-console-comes-online
date: 2026-04-26
title: Console comes online — process editor, knowledge wizard, native trackers
prs: []
summary: The first week the methodology had a real surface. Process editor with FSM config, knowledge import wizard, native Linear / Notion / GitLab / Azure / Jira / Confluence integrations. The CLI stopped being the only entry point.
kicker: Release
---

For three weeks Ship had been a CLI sitting on top of a methodology stack that lived in YAML. This was the week the **operator UI** showed up — a Next.js console with a process editor, a knowledge import wizard, a workspace ops dashboard, and direct OAuth-or-PAT integrations to the trackers and code hosts the methodology assumes. Everything still runs from `shipctl` for engineers; the console is what a product owner opens.

The PR-flow only started landing late this week, so most of these are direct `main` commits — proper PR-numbered entries start in the next release.

## Highlights

### Process editor: agent profiles, role catalog, transitions

The `process` editor surface picked up nine distinct features this week. State editor with role selector. Transition editing scoped to the selected state. Editable state keys. Specialist prompt and exit contracts on the role. Agent-profile selector per state. Specialist role catalog. Validation on the process schema. Persisted FSM config edits. Persisted canvas layout. By Friday the process map was a real editable thing rather than a read-only diagram.

### Knowledge import wizard

Two PRs added a guided knowledge-import flow to the console — pick a source, scope it, run it, see what came back. The Distiller (the LLM-backed ingest classifier) now has a button instead of just a CLI invocation.

Procedural patterns started seeding into the catalog as knowledge — the codified versions of the methodology, queryable from the agent.

### Native integrations

Linear, Notion, GitHub, GitLab, Azure DevOps, Jira, Confluence — all six trackers and code hosts the methodology assumes got native install flows this week, with native credential paths instead of "paste this token here". Notion learned to create tickets through data sources. Linear got native credentials. GitLab and Azure DevOps connect via PAT flows for now (OAuth coming).

### Console basics

`add ops dashboard`. `add process view`. `add app shell`. The console moved from "scaffolding next to the CLI" to "the place a workspace operator lives".

## Improvements

- `process:` streamline editor canvas
- `process:` reshape process canvas; make process map visible
- `process:` add layout editor; persist canvas layout
- `process:` make editor repo-backed
- `process:` align review owner role id
- `process:` clean up config proposal flow; trim editor cleanup leftovers
- `process:` add draft change controls
- `process:` use inbox-created timestamp
- `console:` make app shell full-width; clean app shell lint
- `console:` show connected tracker cards
- `integrations:` refresh native provider health
- `integrations:` use native Linear credentials
- `integrations:` add Jira and Confluence adapters
- `integrations:` connect GitLab PAT
- `integrations:` connect Azure DevOps PAT
- `integrations:` add native provider installs
- `integrations:` expand native setup flows
- `integrations:` disable native providers (kill switch)
- `cli:` validate process schema fields
- `cli:` prefer workspace API base for Ship actions
- `workflows:` isolate Ship repo env for scheduled lanes
- `catalog:` seed procedural patterns as knowledge
- `ship:` install wizard-default bundle; release 0.12.0

## Fixes

- `process:` validate agent profile config; validate process schema fields
- `process:` commit canvas layout on drag end
- `notion:` create tickets through data sources
- `integrations:` handle Notion OAuth persistence; allow Jira tracker refs
- `console:` use OAuth for Notion integrations
