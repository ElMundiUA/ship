---
title: The mapping table the runtime never read
slug: the-mapping-table-the-runtime-never-read
date: 2026-05-06
kicker: Autopsy
description: We shipped an editor surface, an LLM resolver, and a PR-write flow for a config field. Twenty-four hours later we walked all of it back. The runtime didn't read the field. The story is about where config belongs.
reading_time: 5
author: Denys Kuzin
tags: [autopsy, config, agents, build-in-public]
---

We shipped a feature on a Tuesday. By Wednesday we'd reverted it. The total diff between "ship" and "revert" was about 1,500 lines added and the same 1,500 deleted. No bug report. No customer escalation. Just a quiet realisation that the code we wrote was perfectly correct and the *premise* was wrong.

The feature was a tracker projection editor. The premise was that operators should map Ship's seven canonical lifecycle states (`backlog`, `planning`, `executing`, …) to their tracker's actual column names — *Linear's "In Progress"* vs *Jira's "In Development"* vs *that team that renamed their column to "Cooking"*. We built it.

A 7×4 table in the process editor. A "Sync with Linear" banner. A backend route that probes the team's workflow, runs a deterministic + LLM hybrid resolver with retry, and opens a PR that patches `process.tracker_mapping` in `.ship/config.yml`. The works.

It worked. Operator clicks button, system probes Linear, model resolves whatever the deterministic pass couldn't, validates the result, opens a PR. Operator merges. Done.

The runtime read none of it.

## The path the runtime actually took

When the CLI runs a routine and the agent decides to move a ticket from `executing` to `reviewing`, the path is:

1. CLI calls `POST /agent-runs/finish` with `stage_next` and a ticket reference.
2. The server resolves the workspace's bound tracker via `Integration.config`.
3. The Linear adapter is constructed from that config — which holds `state_id_by_name`, populated by the OAuth provisioner the moment the operator first connected Linear.
4. The adapter calls Linear's GraphQL API with the right state ID.

Notice the path doesn't go anywhere near `.ship/config.yml`. The mapping the runtime needed was already on disk — *in the database*, on the `Integration` row, written once at OAuth time. The YAML field we'd just spent a day building an editor for was decorative. Operators were editing dead config.

We caught it in review the next morning. The reviewer asked an obvious question: *"where does the runtime read this?"* And the answer was: it doesn't. Anywhere.

## How we got there

The mistake was reasonable, which is the worst kind. We had a real problem — operators with non-default Linear workflows whose canonical-to-native mapping needed customisation. We had a real surface — the process editor, where the operator was already configuring everything else about the process. We had a real tool — an LLM resolver that could look at the team's actual workflow and propose a sensible mapping.

We just put the output in the wrong place.

The seductive part was that the YAML *seemed* like the right home. It's where the rest of the process config lives. It version-controls cleanly. It diffs in pull requests, which is the right review surface for a sensitive change. Every signal said "the YAML is the operator's tunable surface, put it there".

What we missed: *who reads it*. The YAML is read by the lanes-sync service, the process editor, and the seed bundle. None of them are the runtime. The runtime reads the database. So the moment the editor sent a PR, two sources of truth existed — the YAML the operator had just edited, and the database the runtime was still reading. The first one shipped instantly. The second one shipped never.

This is the rule we should have started from: **config belongs where it's read, not where it looks editable.**

## The actual layering

There are three places config can live, and they have non-overlapping jobs:

**Adapter constants.** Baked-in defaults that ship with the code. Linear's default workflow, Jira's default statuses, GitHub Issues' open/closed semantics. These don't change per-customer; they change when the *vendor* changes their product. Source of truth: the codebase.

**Integration row in the database.** Per-team customisations that need to exist before the runtime can do anything. The OAuth provisioner writes them once when the operator connects the tracker — `state_id_by_name`, label IDs, signal labels, *and now* `canonical_to_native` for teams whose workflow drifted from default. Source of truth: the row that gets written by the OAuth callback.

**`.ship/config.yml`.** Operator-authored declarations of what the *process* looks like — stages, transitions, routines, schedules. Things the operator should actually be reading and editing. Source of truth: a pull request the team reviewed.

The mapping we built belonged in the second tier. We put it in the third. The fix wasn't to make the third tier work harder; it was to move the resolver into the OAuth provisioner and let the second tier carry it. That's where the next commit went, and that's where the mapping lives now — invisible to operators, computed once, persisted on the integration row.

## What we kept

We didn't lose work. The deterministic pass, the LLM resolver, the per-slot retry, the validation rules — those are all still in the repo. They just got mounted differently. Instead of a route the operator hits from a banner, they're now a function the OAuth provisioner calls when a team connects Linear. The operator doesn't see the table. The mapping shows up correctly the moment the integration is bound.

Net effect: the user-facing surface got *smaller*. One fewer thing for the operator to learn, one fewer surface for them to misconfigure, one fewer PR template polluting their repo history. And the runtime got *more reliable*, because there's now exactly one place the canonical mapping lives.

## The lesson, as a one-liner

When you find yourself building an editor surface for config, ask: *which process reads this, and is the answer "the runtime"?*

If yes, the editor doesn't belong in user-space. The provisioner does. **The operator's job is to declare intent. The system's job is to derive the mapping.**

If no — if the runtime really doesn't care, only some report or audit trail does — then maybe the field shouldn't exist at all.

We're now down one editor surface, one route, one banner, one Next.js proxy, one validator, and about a thousand lines of LLM plumbing that lives in a different module. The runtime ships the same behaviour. The operator does less. That's the right shape.
