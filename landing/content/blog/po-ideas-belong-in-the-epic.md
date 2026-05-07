---
title: PO ideas belong in the epic, not the ticket
slug: po-ideas-belong-in-the-epic
date: 2026-05-03
kicker: Best practice
description: When the agent that writes the ticket and the agent that builds it are different processes, scope and motivation have to live somewhere both of them can read. We tried chat. We tried fat tickets. The thing that actually works is putting it in the project description and keeping the tickets thin.
reading_time: 6
author: Denys Kuzin
tags: [planning, agents, ux, build-in-public]
---

Most product orgs accumulate the same artefact three times. Once in a chat thread where the PO and the tech lead argued out the shape. Once in the description of the parent ticket the PO ended up filing. Once in the bodies of the four child tickets the team broke it into. By the time the agent reading the child tickets gets there, two of the three copies have drifted, the chat is gone, and nobody is sure which version is real.

We rebuilt Navigator's planning surface today around a rule that sounds boring and isn't: **PO ideas live in the project description, not in the chat or the tickets.**

## Why the chat is the wrong home

Chat is where ideas form. It is not where they should live.

A planning session in chat looks great in the moment. The PO and the agent volley back and forth, scope crystallises, decisions get made. Then the session ends, the chat scrolls off, and the team that has to actually build the thing reads four tickets that say "implement X" without ever seeing why. The "why" was in the chat. The chat is gone.

Chat-as-source-of-truth has a second failure mode that's worse. The agent itself can't pull motivation back when working a child ticket. It has access to the ticket body, not the conversation that produced the ticket body. When motivation is missing, the agent has to either ask ("a clarification on every ticket gets old fast") or guess ("which is how you ship the wrong thing"). Either way the chat-only pattern teaches the team to stop trusting the agent for non-trivial work.

## Why fat tickets are the wrong home

The next thing teams try is putting everything in the ticket. The ticket body explains the goal, the motivation, the constraints, the open questions, the prior decisions, the alternatives considered. It's twelve hundred words. It's the size of a short blog post.

This solves the agent's "where do I read motivation" problem. It creates four new ones.

It duplicates. A fat ticket only stays useful as long as it's the only ticket. The moment the work breaks down into children, every child either copies the parent's motivation (now you have N copies that drift) or doesn't (now the children are useless without the parent open in another tab).

It punishes scoping. A PO who knows their motivation will be repeated in five tickets writes shorter tickets and skips the motivation entirely. The opposite of what we wanted.

It buries decisions. Decisions made on the parent ticket — "we're not doing X because of Y" — get stuck in a body the team only re-reads when they file the next sibling. By the time someone challenges the decision two months later, the reason is buried in a wall of text nobody scrolls back to.

## Why the project description is the right home

Linear projects, Jira epics, GitHub Project v2 items — the trackers we integrate with all have a place that sits one level above the ticket and is shared by all its children. That place is structured for prose, has its own version history, has its own permissions, and is exactly the canvas a PO needs.

Three properties make it the right home.

It is shared. Every child ticket links back to the project. The agent working any ticket can read the project body, no extra plumbing. The PO who writes a new idea writes it once and every ticket inherits it. There is no drift because there is no copy.

It accumulates. A project lives across many planning sessions. New constraints get appended, not retyped. Decisions and their reasoning pile up over the lifetime of the initiative, in chronological order, where everyone can see them. The body grows the way a real engineering doc grows — by addition, with history.

It separates concerns. Motivation, scope, constraints, and decisions belong in the project. The ticket gets to be small: goal in one paragraph, acceptance criteria in five bullets, a link to the project. The agent reads both. The PO writes both, but writes the heavy stuff once.

> The point of the project description is not "more documentation." It's "fewer copies of the same documentation."

## What this looks like in Navigator

We rewrote Navigator's planning surface around this rule. The agent has four new tools — `list_projects`, `get_project`, `create_project`, `append_project_description` — and a single new flag on `create_ticket` (`project_id`) that attaches a child ticket to its epic on creation.

The system prompt for the planning scenario reads, almost verbatim:

> PO insights, scope, motivation, constraints, decisions → project description (epic body); tickets stay short (goal + AC) and link to project.

The flow on a new initiative now goes: list existing projects → either pick one or propose a new project body in chat → write the project → file child tickets that link to it. PO ideas that come up across sessions land in `append_project_description` so the body grows session over session.

There is a second-order effect. Once the project description is the canonical surface, the chat session producing it becomes disposable. We can pack the chat as a knowledge bucket and forget the transcript — the durable artefact lives in the tracker, not in our agent memory. That's the inversion that makes the whole loop work: the agent stops being the keeper of context and starts being a tool that reads context the same way the team does.

## The smaller rule that follows

Once tickets are thin, they get easier to read, easier to estimate, easier to swap between agents. A child ticket that says "implement the project_id parameter on create_ticket; AC: existing tests pass + project linkage shows up in Linear" is something a junior agent (or a junior engineer) can pick up cleanly. A child ticket that says "do the planning step from the ELS-52 epic" is exactly as useful, and saves you from dragging eight paragraphs of motivation into the ticket body to make it self-contained.

The rule is not "use the project description more." It is **stop putting motivation where it has to be duplicated.** Once you accept that constraint, the project description is just where it ends up.
