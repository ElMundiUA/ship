---
title: Clarification is not failure
slug: clarification-is-not-failure
date: 2026-05-01
kicker: Best practice
description: Most agent loops treat "I have a question" as the same outcome as "I crashed." Both stop the pipeline. Both look red on a dashboard. They are completely different things, and conflating them teaches agents to invent rather than ask.
reading_time: 5
author: Denys Kuzin
tags: [agents, best-practice, fsm]
---

The first thing most agent loops get wrong is treating a question as a failure.

A run that ends with "I cannot proceed because the environment is broken" is not the same kind of run as one that ends with "I have four questions for the human before I write anything." In a healthy system, those are different outcomes with different downstream behaviour. In most systems we have looked at, including ours until this week, they collapse into one bucket called "blocked" or "needs human" or, worst of all, "error."

Conflating the two is how teams accidentally train their agents to stop asking.

## The four shapes of finishing

A round of agent work ends in one of a small number of shapes. Naming them matters more than people expect.

**Done well.** The agent finished its role on this ticket. The next role can pick up. The pipeline moves forward. This is the bulk of runs in a workspace where the policies and patterns are tuned.

**Done with question.** The agent did its work — it read the ticket, formed an opinion, identified the ambiguity — and surfaced a question that has to be answered before the next role can usefully move. The pipeline pauses on this ticket. Other tickets keep flowing.

**Cannot do.** The agent could not run. A secret was missing, an adapter was broken, a branch was conflicted. The pipeline does not pause because of this ticket; it pauses because of the environment. Other tickets in the same FSM stage face the same wall.

**Out of scope.** The ticket is not a ticket. It is spam, a duplicate, a reminder somebody pasted into the wrong tool. The right move is to close it, not to keep it in the queue. The pipeline never had a job here.

These four shapes have different operators, different freshness windows, different escalation paths, and different effects on the rest of the queue. Telling them apart is not a luxury. It is the reason the queue is not a panic room.

## What "blocked" hides

Most pipelines we have read pile two of those four into a single state called blocked. They merge "cannot do" and "done with question" into one row in an inbox or one label on a ticket.

The merge looks economical. It is expensive in three ways.

It hides workload. A workspace with four tickets waiting on a human's answer and zero tickets blocked on infrastructure is a healthy workspace with active intake. A workspace with zero tickets waiting on a human and four tickets blocked on infrastructure is a workspace with broken plumbing. They look identical when both states are flattened to "blocked: 4."

It hides ownership. A clarification has an owner — the human who is supposed to answer. A real block has a different owner — the operator who maintains the environment. Mixing the two means every "blocked" item gets read by both people, twice, slowly. Or, more commonly, by neither.

It teaches the agent the wrong escape. If the system reacts to "I have a question" the same way it reacts to "I crashed," the cheapest behaviour for the agent is to not ask the question. Make a guess. Pick a default. Pick the most charitable interpretation and run with it. That is how agents that started useful become hallucination machines: every time they paused to ask, the surrounding system rewarded them for not pausing.

> If clarification looks like failure on the dashboard, the agent learns to skip the question.

## What "done with question" deserves

A clarification is a first-class outcome. It deserves its own label, its own filter, its own freshness window, its own escalation path.

In Ship, a `needs:clarification` label on a ticket does four things at once.

It removes the ticket from the intake stage's pick filter, so the intake agent does not loop on the same ambiguity every fifteen minutes. The agent already asked. Asking again is noise. The ticket re-enters the queue when the human removes the label, which is the human's signal that an answer is in the comments.

It surfaces the ticket in the operator's inbox as a clarification row, with the agent's question already in the summary. The two-minute rule applies: an operator should be able to read the row and know what is being asked, what evidence the agent had, and what would unblock the pipeline.

It distinguishes the ticket from the broken-environment bucket on every dashboard the operator looks at. A spike in clarifications is intake working — agents asking, humans answering, ambiguity flowing through the front door instead of the rear. A spike in real blocks is something to fix, this week.

It does not move the ticket out of Todo. The status reflects "this work has not started" because, from the FSM's perspective, it has not. Intake has produced a question, not a deliverable. Clarification is a hold, not a step.

That is four behaviours, none of them automatic, all of them worth wiring once and never thinking about again.

## What a real intake question looks like

The intake role's job is not to be the smartest reader of the ticket. It is to be the most consistent reader. Every ticket gets the same five-minute pass: classify it, list the assumptions you would have to make to proceed, decide whether those assumptions are safe.

If the assumptions are safe, the role finishes with `done well` and the next role takes over.

If even one assumption is load-bearing and the ticket does not say anything about it, the role finishes with `done with question`. The question has the format that earns a two-minute answer: name the ambiguity, propose two or three likely interpretations, and ask which one is wanted.

A clarification is not "I do not understand." A clarification is "I understand four ways. Pick one."

The difference between those two formulations is the difference between a clarification that takes ninety seconds and a clarification that takes three days.

## What clarification is not

It is not a stalling tactic. An agent that returns five clarifications on a ticket whose ambiguity is one human-readable paragraph is failing the role, not asking well. The two-minute rule applies in both directions: if the answer to the agent's question would take an hour to write, the agent is asking the wrong question.

It is not a substitute for opinion. The intake role is allowed to have a recommendation. A good clarification names the ambiguity, names the agent's preferred interpretation, and asks the human to confirm or correct. Asking neutral five-way questions is not humility; it is laziness dressed as deference.

It is not a substitute for the ticket. If a ticket is short and bad, the answer is to ask for one good rewrite, not to comment on every missing field. Operators recognise the difference. Inbox attention is finite.

## What this looks like in practice

A team that takes clarification seriously usually ends up with a queue that breathes. New tickets land in Backlog. Humans triage them into Todo when they are workable. Intake picks them up. Most of them go straight through to BA and beyond. A real fraction — anywhere from ten to thirty percent in the workspaces we have watched — bounce back as clarifications. A human answers. The clarification clears. The ticket re-enters intake. The fraction that bounce back twice is small. The fraction that bounce back three times is a sign the ticket should be rewritten or closed.

The interesting metric is not the absolute clarification rate. It is the clarification half-life: from the moment the agent asks to the moment the human answers, how long does the ticket sit? In a workspace tuned for it, that number is hours. In a workspace where clarifications and blocks share a bucket, that number is days, because the operator has to triage every entry before they know whether to answer it or to fix the plumbing.

A small change in routing produces a big change in cadence.

## The principle

Treat clarification as the most valuable signal your agents send.

It is the moment the agent says: I have read this carefully, I see the shape, and there is a piece of it that only a human can decide. It is the agent doing exactly the job you wanted it to do. Punishing it — even by accident, by mixing it with broken-environment alerts — teaches the agent that asking is a strategy with a cost. The first thing it will optimise is fewer questions. The second thing is wronger answers.

Give the question its own outcome. Give the question its own label. Give the question its own row in the inbox. Give the question its own filter so the agent does not re-ask. Give the answer its own protocol — a removed label, a comment, a re-pick — so the loop closes cleanly.

The cost of doing this once is small. The benefit compounds, because the agents in the next role over are now reading tickets whose ambiguity has already been resolved, by a human who answered a question that had only one good answer, in the time it took to read it.

That is what intake is for.
