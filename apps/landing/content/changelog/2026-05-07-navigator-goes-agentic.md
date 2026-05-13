---
slug: 2026-05-07-navigator-goes-agentic
date: 2026-05-07
title: Navigator goes agentic
summary: A subagent tool, a tighter operating-rules prompt, and a fleet of read-and-write tools — all under the same chat surface, all bounded by workspace policy.
kicker: Release
prs: [142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 172, 174, 178, 179, 180, 181, 184, 186, 187, 188, 189, 190]
---

This is the week the Navigator stopped being a chat shell with tools and started being an agent that **decides which agent to be**. Three PRs in one stack: a session-enrichment pass, a system prompt that names the operating rules, and a `consult_specialist` tool that hands a focused task off to a sub-agent with its own role prompt and the same workspace tools. Every move is still scoped by the workspace policy; the human can see who decided, why, and whether the rules held.

Alongside that, a complete rebuild of the knowledge layer — claim store, extractor cron, topic-view renderer, decay-and-revive — and a brand-new "project-first" delivery flow that walks decomposition (BA → Architect → QA → Developer) before any code is touched.

## Highlights

### Navigator: agentic loop in three PRs

A staged rewrite ([#153](https://github.com/ElMundiUA/ship/pull/153) → [#154](https://github.com/ElMundiUA/ship/pull/154) → [#155](https://github.com/ElMundiUA/ship/pull/155)) that lifted the chat from "tools called from a prompt" to a proper agentic loop. PR1 enriched the session context with the open ticket, project, recent inbox items, and tracker state — so the model isn't operating blind. PR2 rewrote the system prompt around explicit operating rules ("you have these tools, you can refuse, you must explain"). PR3 added `consult_specialist` — Navigator can now hand a focused task to an isolated sub-agent (BA, Architect, Developer, etc.) with the right role prompt and the same workspace tools, without inheriting Navigator's context.

### A leaner, sharper toolbox

Four redundant tools were retired ([#157](https://github.com/ElMundiUA/ship/pull/157)) and KB search got consolidated ([#158](https://github.com/ElMundiUA/ship/pull/158)) so the model isn't picking between three near-equivalents. Then six new tools landed in two PRs: read-only ([#160](https://github.com/ElMundiUA/ship/pull/160)) — `get_ticket`, `get_dashboard`, `workspace_audit_search` — and mutating ([#161](https://github.com/ElMundiUA/ship/pull/161)) — `update_ticket`, `set_priority_state`, `start_decomposition`. Each mutating tool is verify-before-mutate; nothing changes until the human says yes (unless the user gave an explicit command).

### Project-first delivery

A four-PR sequence ([#142](https://github.com/ElMundiUA/ship/pull/142), [#143](https://github.com/ElMundiUA/ship/pull/143), [#145](https://github.com/ElMundiUA/ship/pull/145), [#147](https://github.com/ElMundiUA/ship/pull/147)) replaced "an agent picks a ticket and writes code" with **"an agent shapes a project, hands it off to specialists, then writes code"**. `create_project` lands an anchor on the dashboard in `Drafts`. Navigator helps shape it. `start_decomposition` walks the BA → Architect → QA → Developer chain through the planning anchor; only when `stage:planning_done` lands does the Drafts row flip Active and the autonomous picker takes over.

### Knowledge layer, end-to-end

A six-phase build of the claim graph: claim store + extractor ([#144](https://github.com/ElMundiUA/ship/pull/144)), reconciliation engine ([#146](https://github.com/ElMundiUA/ship/pull/146)), topic-view renderer ([#149](https://github.com/ElMundiUA/ship/pull/149)), read API ([#150](https://github.com/ElMundiUA/ship/pull/150)), decay + auto-revive ([#151](https://github.com/ElMundiUA/ship/pull/151)), batch extractor with the Anthropic preamble fix ([#152](https://github.com/ElMundiUA/ship/pull/152)). The console stopped reading legacy buckets, started reading topic views ([#163](https://github.com/ElMundiUA/ship/pull/163)), got a real topic detail page ([#164](https://github.com/ElMundiUA/ship/pull/164)), and finally an editorial layout for the index ([#165](https://github.com/ElMundiUA/ship/pull/165)).

### Agent runs: picker hardening

Eight PRs that turn the autonomous picker from a "tries hard, sometimes runs the wrong thing" worker into a deterministic, contention-safe one. Orphan tickets get rejected ([#169](https://github.com/ElMundiUA/ship/pull/169)). The picker only runs on projects whose priority state is Active ([#184](https://github.com/ElMundiUA/ship/pull/184)). Parent project bodies inject into the SDLC startup context ([#186](https://github.com/ElMundiUA/ship/pull/186)). Overlay-frozen tickets — ones in clarification or blocked — are filtered out ([#187](https://github.com/ElMundiUA/ship/pull/187)). Drafts flip to Parked at `planning_done` ([#188](https://github.com/ElMundiUA/ship/pull/188)). A pick-race becomes impossible thanks to advisory transactional locks ([#189](https://github.com/ElMundiUA/ship/pull/189)). Linear-native projects auto-onboard onto the dashboard ([#190](https://github.com/ElMundiUA/ship/pull/190)).

### Repo symbols on demand

A new tree-sitter parser ([#174](https://github.com/ElMundiUA/ship/pull/174)) — when a specialist needs to know the shape of a file (functions, classes, exports), it calls `repo_symbols` and gets a structured map without re-walking the repo on every turn. Cuts startup latency for code-touching agents in half.

## Improvements

- `processes:` decomposition is now a first-class peer process, not an embedded sub-flow ([#167](https://github.com/ElMundiUA/ship/pull/167))
- `priorities:` child ticket states sync with the parent project state in Linear ([#181](https://github.com/ElMundiUA/ship/pull/181)); new projects default to `planning` ([#172](https://github.com/ElMundiUA/ship/pull/172))
- `agent_roles:` Navigator system prompt no longer references retired tools ([#178](https://github.com/ElMundiUA/ship/pull/178))
- `landing:` refreshed dogfood numbers — 30 days, 608 commits, claim-graph live ([#183](https://github.com/ElMundiUA/ship/pull/183))
- `docs:` agent-launch pre-flight smoke runbook ([#179](https://github.com/ElMundiUA/ship/pull/179))
- `notion:` walker soft-skips `child_database` blocks with no accessible data sources ([#159](https://github.com/ElMundiUA/ship/pull/159))

## Fixes

- `linear:` intake's empty-label exclusion fired incorrectly on labelled-but-empty issues ([#180](https://github.com/ElMundiUA/ship/pull/180))
- `knowledge:` ingestion sync survives long-running connector walks ([#168](https://github.com/ElMundiUA/ship/pull/168))
- `reconciler:` real pgvector embeddings round-trip correctly through numpy + binary codec ([#162](https://github.com/ElMundiUA/ship/pull/162))
- `fix:` default Anthropic Haiku model id bumped to the post-preview stable ([#156](https://github.com/ElMundiUA/ship/pull/156))
- `hotfix:` prioritizer handoff hooks hoisted above empty-state returns ([#148](https://github.com/ElMundiUA/ship/pull/148))
