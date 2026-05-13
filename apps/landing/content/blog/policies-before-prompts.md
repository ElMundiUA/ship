---
title: Policies before prompts
slug: policies-before-prompts
date: 2026-04-28
kicker: Best practice
description: A good prompt asks for work. A good policy says what kind of work is allowed, what proof is required, and when the machine must stop.
reading_time: 6
author: Denys Kuzin
tags: [best-practice, policies, governance]
---

Most teams start agent adoption by improving prompts.

They make the prompt longer. Then more specific. Then more polite. Then full of rules. Then the prompt becomes a small constitution copied into five places and nobody knows which clause the agent actually obeyed.

That is the wrong container.

Prompts are for the task. Policies are for the boundary.

If the boundary lives inside the prompt, every task becomes a chance to forget it.

## A policy is not a manifesto

The useful version is small.

A policy answers four questions:

1. What is in scope?
2. What is forbidden?
3. What evidence is required?
4. When does a human decide?

That is it.

Not values. Not principles. Not "be careful with production." A policy should be boring enough that a reviewer can check it and an agent can follow it.

Bad policy:

> Use good judgement and be careful with risky changes.

Good policy:

> Do not edit payment, authentication, or migration files without an owner approval in the Inbox. Any PR touching those paths must include the linked ticket, the failing or passing check, and the reviewer note that approved the scope.

One of those is an aspiration. The other is a fence.

## The prompt is too late

By the time a prompt is written, the user already wants the work to happen.

That is the worst moment to negotiate safety. The owner is thinking about the outcome, the engineer is thinking about the diff, and the agent is optimized to produce motion. If the boundary is still implicit, the path of least resistance is to keep moving.

Policies move that negotiation earlier.

Before the task:

- which repos may be touched;
- which file paths are sensitive;
- which tracker states are allowed to launch automation;
- which checks must pass;
- which decisions need a human.

Now the agent run is not a negotiation. It is an execution under a known rule.

> The best safety rule is the one you do not have to remember during the run.

Remembering is a weak control. A visible policy is a stronger one.

## The owner needs policy more than the agent does

It is tempting to think policies exist for machines.

They do not.

Policies exist so a human can inspect the machine without replaying the whole story in their head. A solo founder looking at a PR at midnight needs to know why this change was allowed to happen and what proof came with it. A product owner reviewing an Inbox item needs to know whether approving it is normal work or a risk exception.

The policy is the shared reference point.

Without it, the reviewer asks:

"Why did the agent do this?"

With it, the reviewer asks:

"Did the agent stay inside the rule?"

That second question is smaller. Smaller questions are how systems stay quiet.

## Four policies worth writing first

Do not start by writing a complete governance framework. Start with the policies that remove the most ambiguity.

**Launch policy.** What makes work eligible for automation? A tracker state, label, owner, priority, or explicit Inbox approval.

**Scope policy.** Which repos, directories, services, or product areas are safe for agent-assisted changes. Which ones are never safe without approval.

**Evidence policy.** What must exist before a task is called done: PR, check, screenshot, trace, comment, release note, or knowledge update.

**Escalation policy.** When the agent must stop: missing context, secrets, ambiguous requirement, sensitive path, failing test it cannot explain, or a change that expands scope.

Those four are enough to make the first week legible.

They are also enough to expose bad automation. If an agent cannot follow a launch policy, scope policy, evidence policy, and escalation policy, the problem is not prompt quality. The problem is that the work is not bounded.

## Keep policies short

Long policies rot.

They rot because nobody reads them during review. They rot because exceptions get patched into paragraphs instead of removed from the workflow. They rot because "comprehensive" feels safer than "used."

A policy should fit in the reviewer's working memory.

If it needs a table, it may still be fine. If it needs a ceremony, it is probably a process document pretending to be a policy.

The best Ship policies look like this:

- scope;
- required evidence;
- escalation rule;
- owner.

Four rows. Not fifty.

## The principle

Do not put standing rules in task prompts.

Prompts change every run. Policies should change under review. Prompts ask the machine to do useful work. Policies decide what useful is allowed to mean.

The practical order is:

1. Write the policy.
2. Connect the repo and tracker.
3. Seed the knowledge.
4. Let the agent act.
5. Review the evidence against the policy.

Most agent failures are not intelligence failures. They are boundary failures.

Write the boundary first.
