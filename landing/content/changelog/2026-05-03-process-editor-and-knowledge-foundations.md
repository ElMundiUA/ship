---
slug: 2026-05-03-process-editor-and-knowledge-foundations
date: 2026-05-03
title: A process editor that's actually editable, and a dashboard that doesn't lie
summary: Process map went from a React-Flow demo to a CSS-grid swim-lane editor with state, drag-drop, and a publish gate. Dashboard prioritizer learned the difference between active, planning, and parked. The console stopped agreeing with itself when reality disagreed.
kicker: Release
prs: [80, 135, 136, 137, 138, 139]
---

The week of polish before the agentic push. Most of the work was in the console: a real process editor (drag-drop, swim-lanes, validation, publish blocking), a dashboard prioritizer that surfaces state buckets and Linear OAuth diagnostics, and a long list of small "actually shipping is what gets you to the next thing" cleanups. The methodology layer got harder boundaries — what's a routine, what's a specialist, where does configuration live — so the upcoming Navigator work could lean on stable footing.

## Highlights

### Process editor: no more React Flow

The visual canvas got rebuilt on CSS Grid + SVG arrows. Two reasons: 86 KB of First-Load JS gone, and **swim-lane layouts that actually correspond to the seven canonical states** (Backlog → Planning → Executing → Reviewing → Awaiting input → Blocked → Closed). Flow canvas is now the projection table's UI; Tracker tab was retired because there was nothing in it that didn't already live under the canvas.

Drag-and-drop reorder inside columns landed in `process: H1`; the SVG arrows that were doing nothing useful were dropped. Inline `+ add stage` button on lane-header hover (`H2`). Visual distinction for specialists vs routines on the Capacity calendar (`H3`).

### Process editor: the validation gate

A configuration with no tracker, no orchestrator, or no default-agent profile is *not* a publishable process. The editor blocks Publish on hard errors and surfaces warnings instead of silently shipping a broken state machine. Hard-stop gating on missing tracker / orchestrator / default agent.

`process: state field on YAML stages` closed the swim-lane bug forever — every stage now declares which canonical state it lives in, so the projection can never put a stage in the wrong column.

### Dashboard prioritizer

Three buckets — `active`, `planning`, `parked` — visible on the dashboard, each with its own meaning ([#139](https://github.com/ElMundiUA/ship/pull/139)). Drag a row between buckets and it actually moves; completed Linear projects stop showing up on the active list ([#138](https://github.com/ElMundiUA/ship/pull/138)). Linear OAuth scopes surface in the empty-state error, not an opaque "list_projects failed" ([#137](https://github.com/ElMundiUA/ship/pull/137)). PAT-vs-OAuth tokens get auto-detected with response bodies surfaced for diagnosis ([#135](https://github.com/ElMundiUA/ship/pull/135)). Blind team-resolution probe gone from `list_projects` ([#136](https://github.com/ElMundiUA/ship/pull/136)).

### Wizard: drift surface + workspace defaults gate

The install wizard learned to detect drift between the seed bundle and what's actually in the workspace, and shows it next to the Linear OAuth-only connection list. A workspace-defaults gate (`tracker / agent / orchestrator`) means brand-new workspaces can't accidentally publish without naming what's connected.

The seed bundle itself shipped at [#80](https://github.com/ElMundiUA/ship/pull/80) — the `wizard-default` install bundle that brand-new workspaces get as a starting point.

## Improvements

- `process:` editor body streams so the shell paints instantly
- `process:` Capacity calendar shows the real cron grid; empty by default
- `process:` tracker mapping bakes into the adapter — no more "15 unmapped" on first attach
- `process:` 3-level model (`process : stage : state`) — canonical lifecycle bucketing across every stage
- `process:` transition.trigger_actor — `user / agent / either`
- `process:` workspace map → CSS card grid (drop React Flow from overview)
- `process:` editor canvas → CSS Grid swim-lanes (drop React Flow)
- `process:` count Ship GitHub App as a ready orchestrator
- `process:` chrome cleanup — page title is the process, banners are pills
- `process:` canonical six routines — fixed in seed, FE, and elship config (forever)
- `wizard:` ripped out stale codeowners/intel UI; rewrote confirm-step bullets
- `integrations:` Linear/Notion are OAuth-only now; raw-key paths killed
- `synth:` archive action — LLM can vote stale articles out of a bucket
- `nav:` `search_buckets` surfaces `article_id` / `article_slug`
- `nav:` pin tool inventory + cross-link prompt to registry
- `agent:` A1 — harden prompt against hallucinated people, dates, IDs
- `secret_probe:` Linear probe exercises Read-issues scope
- `chat:` `include_archived` now surfaces dual-flip archived rows too

## Fixes

- `inbox:` row actions go through form route handlers (fix client/server-only split)
- `chat:` post-end scroll-down jolt is gone
- `chat:` real conversation title; no more blink/jerk
- `process:` editor is repo-backed; reseed CI secrets repaired
- `settings:` any one endpoint hiccup no longer collapses the whole shell
