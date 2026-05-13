---
title: Boring failures are good failures
slug: boring-failures-are-good-failures
date: 2026-05-06
kicker: Best practice
description: The worst failure mode for an agent isn't a crash — it's silence. The pipeline keeps running, the dashboard stays green, and the work that was supposed to happen quietly didn't. The cure is to make failure modes mundane and named, before the agent ever runs.
reading_time: 4
author: Denys Kuzin
tags: [agents, best-practice, reliability]
---

The worst thing an agent can do isn't crash. It's *finish*. Run for forty seconds, post a confident comment that doesn't quite address the problem, exit zero, leave the ticket pinned where it was, and move on. The pipeline stays green. The dashboard stays green. Nobody pages anyone. And the work that was supposed to happen quietly didn't.

This is the failure mode you can't grep for. It's also the one most agent stacks ship by default, because the well-meaning instinct is to make agents *try harder* when they're stuck. Try harder means: keep going, find a path, post something. Which means: when the agent doesn't know what to do, it makes something up.

The fix is not to make the agent better at improvising. The fix is to make the *failure modes* better — predictable, named, and easy to fix.

## What boring failures look like

A boring failure has a one-line shape:

> Invariant `<name>` violated.
>
> Step `<step>` refused to advance because the proof was missing.
>
> Contract `<contract>` requires `<field>`; got nothing.

Compare to the surprising version:

> The agent commented "I've reviewed the issue and would suggest the team consider these next steps."

The first three tell you *exactly* what's wrong, and the path to fixing it is the same one you used last time it happened. The fourth one tells you nothing. It might mean the agent did the work. It might mean the agent didn't. Reading the comment doesn't tell you which. You have to dig.

A team can recover from a thousand boring failures a day. A team can't recover from one surprising failure a week, because each one costs an hour of digging.

## Where boring failures come from

They come from *contracts*, not from cleverness.

Before the agent runs, the system declares: *to finish in state `ready_next_step`, you must have set `stage_next` and either pushed a branch or set `description`*. To finish in state `blocked`, you must have a `comment` with the reason. To finish in `needs_clarification`, you must have a `ticket_ref` and the comment must be a question.

These are not soft requirements. They're enforced *server-side*, after the agent's run, before any tracker mutation happens. If the agent posts a finish payload that doesn't match a contract, the server rejects it with a named code: `stage_next_required`, `ticket_ref_required`, `needs_clarification_unsupported_for_kind`.

The server doesn't get clever. It doesn't try to figure out what the agent meant. It says no, and the failure is exactly as boring as the contract description.

## What contracts replace

They replace *prompt-only reliability*. The instinct, when an agent is sometimes wrong, is to add more instructions to its prompt. *Make sure to call the finish endpoint. Don't echo the token. Always set stage_next.* The prompt grows. The agent gets less reliable, because longer prompts make it harder for the model to find the actual instruction in the noise. And the rules still aren't enforced — they're just hopes.

A contract is a *mechanism*. It runs whether the prompt was perfect or not. If the agent didn't set `stage_next`, the server rejects the finish call. The agent's next prompt iteration includes the rejection reason. The pipeline doesn't move forward in a half-broken state.

The agent isn't smarter; it's *fenced*. That's the engineering inversion: stop teaching the agent to handle every case, start narrowing the cases it's allowed to be in.

## The four outcomes

Four outcomes is the right number for most pipelines. We landed on:

- **`ready_next_step`** — the role finished cleanly. Move the ticket forward.
- **`blocked`** — the environment is broken. Page someone.
- **`needs_clarification`** — a human needs to answer. Pin the ticket; queue the question.
- **`rework`** *(or its variant)* — the work isn't right yet. Send the ticket back to a previous stage with an explanation.

That's the whole vocabulary. The agent picks one. The server enforces the contract for whichever one was picked. There is no fifth "I tried but I'm not sure" outcome, because that's the surprising-failure machine. If you're not sure, you're `needs_clarification`. If you can't run, you're `blocked`. If the work isn't right, you're `rework`. *Pick one.*

The agent that says *"I've reviewed the issue and would suggest the team consider these next steps"* fails the contract. The pipeline stops. The operator sees a named error. They fix it.

That's a great Tuesday morning.

## The smell test

Look at your last week of agent runs. Count how many fell into one of these:

- The pipeline says "ok" but the ticket didn't move.
- The agent posted a comment that's hard to evaluate without reading the code.
- A run took 45 seconds and produced nothing you can point at.

Each of these is a surprising failure. Each one has a corresponding contract you didn't enforce. The agent isn't broken; *the fence isn't there*.

Add the fence. Watch the same failure happen again, but this time it shows up as `contract_violation: stage_next_required` in the audit log, on the first run after deploy. You fix it once. You're done.

Boring failures are *fixable* failures. Surprising failures mean you didn't have a fence. Make the failures dull and you've made the system reliable.
