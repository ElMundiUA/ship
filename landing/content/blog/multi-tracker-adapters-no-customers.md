---
title: Multi-tracker adapters before there were customers
slug: multi-tracker-adapters-no-customers
date: 2026-04-14
kicker: Architecture
description: We shipped Linear, GitHub Issues, Notion, Jira, and Asana adapters before any of them had a real user. It is the exception to the "build for one before many" rule. Here is why we did it anyway and what kept the cost down.
reading_time: 4
author: Denys Kuzin
tags: [architecture, integrations, build-in-public]
---

The default rule for a pre-revenue startup is *build for one before many*. Pick one customer, one platform, one shape, and saturate it before generalizing. Generalization is expensive and most of the variation will be wrong.

We violated this rule on April 14. The commit body says *feat(cli): ship-agent multi-tracker adapters and docs*. Five tracker integrations in one commit, none of them with a paying user. Here is why, and the small structural choice that kept the cost manageable.

## Why we did it

The methodology *only works* if Ship is the layer above the tracker, not glued to it. Our positioning was always "vendors are plugs rather than the story" — the line is in the README — and that positioning is meaningless if the only plug is Linear.

If we shipped a closed beta with Linear-only, every conversation with a buyer would start with: *but we use Jira.* The methodology would have to defend itself as a *Linear add-on*, not as a delivery system. We'd have already lost the framing.

So adapters first, customers second, even though customers second is the harder problem.

## How we kept the cost down

Two structural moves.

**One canonical state model, seven slots.** Every adapter resolves its native states (Linear's `In Progress`, Jira's `In Development`, Asana's `Doing`) into the same seven canonical buckets — Backlog, Planning, Executing, Reviewing, Awaiting Input, Blocked, Closed. The methodology operates on canonical states; the adapter does the round-trip. This means the methodology doesn't grow per-tracker code paths. Adding a sixth tracker is a translation table, not an algorithm change.

**Adapter as a thin trait, not a deep abstraction.** The adapter interface has six methods: `fetch_issues`, `fetch_workflow_states`, `update_state`, `add_comment`, `find_by_label`, `health_check`. That's it. Anything trickier — webhooks, custom fields, ACLs — is per-adapter, lives in the adapter file, and doesn't leak into the rest of the codebase. The methodology doesn't know which tracker is connected; it asks the adapter and trusts the answer.

These two together meant adding an adapter was about 200 lines plus a config schema. Five adapters fit in one PR.

## What we got wrong

We thought the adapters would all be roughly equivalent in usefulness. They were not. Three months in:

- **Linear**: dominant, validated end-to-end, the reference implementation
- **GitHub Issues**: validated, second most-used in pilots
- **Notion**: validated, surprising adoption from PM-heavy teams
- **Jira**: behind a feature flag, still works, but the workflow customization landscape is so wide that "validated end-to-end" requires per-instance testing
- **Asana**: planned, kept the adapter dormant; not enough demand to invest in QA-ing it

We've kept the dormant ones in `code` because the cost of keeping them is one CI run and the *negotiating* value of having them is real. Buyers see "five trackers" on the matrix, even if four are partial.

## The lesson

The "build for one" rule is right most of the time. The exception is when **the *plurality* itself is the value proposition**. If your positioning is "we sit above any tracker," shipping with one tracker is shipping a different positioning by accident.

Two structural choices kept the cost low: a canonical state model the methodology operates on, and a thin adapter interface so per-tracker complexity doesn't leak. If your positioning needs plurality, those are the two moves to make first.

If your positioning doesn't need plurality, just go build for one. The rule's right.
