---
title: The Inbox is not a backlog
slug: the-inbox-is-not-a-backlog
date: 2026-04-27
kicker: Best practice
description: A backlog stores work. An Inbox stores attention. Mixing the two is how teams turn every agent question into another queue.
reading_time: 6
author: Denys Kuzin
tags: [best-practice, inbox, operations]
---

The fastest way to ruin an Inbox is to treat it like a backlog.

A backlog is where work waits. An Inbox is where attention waits. Those are different things, and confusing them creates the exact mess Ship is supposed to reduce.

The backlog can be long. It can be aspirational. It can contain ideas that will never ship. A good backlog is allowed to be a parking lot.

An Inbox is not.

An Inbox item should be a decision, clarification, approval, failure, or improvement that needs an owner. If it does not need attention, it does not belong there. If it needs work, it should point to work somewhere else.

## Backlogs accumulate. Inboxes decay.

Backlogs get stale slowly. Inboxes get stale fast.

A six-month-old feature idea may still be useful. A six-day-old approval request is usually a smell. A two-day-old failed agent run may already be misleading because the branch moved, the ticket changed, or the policy got updated.

That is why Inbox count is a different metric from backlog count.

A big backlog may be strategy, neglect, or ambition. You cannot tell from the number.

A big Inbox is almost always a broken attention system.

> The Inbox is not where work lives. It is where blocked ownership becomes visible.

If nobody owns the next decision, the item should not be quietly aging. It should be escalated, closed, or turned into tracked work.

## What belongs in the Inbox

Five shapes belong there.

**Clarification.** The agent or reviewer cannot safely continue because intent is ambiguous. "Should this copy say customers or users?" "Is this migration allowed in this release?"

**Approval.** The work is ready to move, but a policy requires a human. "This touches auth." "This changes pricing text." "This expands scope."

**Failure.** Something ran and did not produce a clean result. The owner needs to decide whether to retry, change scope, fix the environment, or stop.

**Improvement.** The system found a better rule, procedure, knowledge note, or workflow shape. This is not urgent work. It is a proposed change to how work happens.

**Exception.** The normal policy does not fit. Exceptions are allowed, but they should be named while they are exceptional, not normalized through silence.

Everything else should go somewhere else.

A bug goes to the tracker.

A feature idea goes to the backlog.

A code change goes to a pull request.

A fact goes to knowledge.

The Inbox points to those things. It does not become those things.

## The two-minute rule

Every Inbox item should be understandable in two minutes.

Not solvable. Understandable.

A good item tells the owner:

- what happened;
- why attention is needed;
- what evidence exists;
- what decision is being asked for;
- what happens next.

If the owner has to open six tabs just to understand the question, the item is not an Inbox item. It is a mystery envelope.

Mystery envelopes create chat. Chat creates folklore. Folklore creates the next mystery envelope.

## Close aggressively

Teams are too polite with Inboxes.

They keep items around because the topic is still important. That is backlog thinking. Importance is not enough. The Inbox is for unresolved attention.

Close the item when:

- the decision was made;
- the work moved into a tracker item;
- the failure was superseded by a newer run;
- the question was answered in knowledge;
- the policy changed and the item no longer applies;
- nobody can name the owner.

That last one sounds harsh. It is the most important one.

If nobody can name the owner, the item is not waiting. It is abandoned. Leaving abandoned items visible teaches the team that the Inbox is decorative.

## Do not optimize for zero

Zero Inbox is not the goal.

Zero can mean the system is quiet. It can also mean the system is not asking for help when it should. A workspace with active automation and no Inbox items ever is suspicious. Either the policies are too loose, failures are hidden, or the team is doing too much decision work in chat.

The goal is not zero. The goal is freshness.

Fresh means:

- new items are clear;
- old items are rare;
- each item has an owner;
- decisions leave evidence;
- closed items teach the system something.

That is a healthier target than a badge that says nothing is pending.

## The principle

Treat the Inbox as a scarce attention surface.

The backlog can hold possibility. The tracker can hold work. Knowledge can hold facts. Pull requests can hold changes. The Inbox should hold the moments where a human owner is needed to keep the system honest.

If an item does not need ownership, move it.

If it needs ownership, make the ask clear.

If the ask is answered, close it.

An Inbox is not a second backlog. It is the place where automation admits it needs a human.
