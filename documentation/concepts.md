# Product concepts

Ship is a workspace for AI-assisted product delivery. The public vocabulary is intentionally small: it should help a product owner understand what is moving, what is blocked, who decides next, and where the evidence lives.

Technical terms still exist in the CLI, API, and RFCs. This page gives the product language first and points to technical reference only when needed.

## Workspace

A **workspace** is the team or product area Ship operates inside. It holds members, connected repos, integrations, policies, knowledge, and the dashboard.

Use a workspace when you want one place to answer:

- Which repos are connected?
- Which tracker and team own the work?
- What is blocked or waiting for a decision?
- What shipped recently?
- Which automations or templates need attention?

## Connected repo

A **connected repo** is a GitHub repository activated in a workspace. Ship can show its health, installed bundle version, pull requests, tracker-linked work, repo secrets, knowledge files, and automation signals.

Connecting a repo does not mean agents can do anything they want. Repo activation is a boundary: it names where Ship may observe and where configured workflows may act.

## Tracker

The **tracker** is the record of product intent. It might be Linear, GitHub Issues, Jira, or another system with the same shape: work item, owner, state, priority, and links to evidence.

The tracker matters because automation should not invent priority. Humans decide what is eligible; machines execute inside that decision.

## Inbox

The **Inbox** is the attention surface. It holds items that need a human decision:

- Clarification: the system needs more context before continuing.
- Improvement: the system found a better way to wire or run something.
- Approval: a policy or workflow needs explicit consent.
- Failure: a configured action failed or degraded repeatedly.
- Exception: the normal rule needs a named override.

Good Inbox discipline means every item has one owner, a visible status, and a disposition: accept, decline, defer, resolve, reassign, or snooze with intent.

## Knowledge

**Knowledge** is the product and repo context agents can use without guessing. It includes code style, test commands, brand rules, runbooks, product constraints, and imported context from connected sources.

Knowledge should be boring and inspectable. Prefer a short maintained article over a long prompt pasted into a chat box. If a repeated clarification keeps appearing, the fix is often to add or update knowledge.

## Automations

An **automation** is repeatable work with a clear scope and trigger. Examples: review pull requests, check release readiness, seed knowledge, inspect dependencies, or run an audit pass.

Automation is not a claim that the machine owns the outcome. It is a bounded action: where it may run, when it may run, what evidence it must leave, and who reviews the result.

## Evidence

**Evidence** is the trail that lets a future teammate understand what happened. It can be a ticket link, PR, CI run, comment, dashboard row, knowledge article, audit event, or configuration change.

If a claim cannot point to evidence, treat it as an opinion. Ship is designed so important actions leave links rather than folklore.

## Assistant

The workspace assistant can answer questions using the connected workspace: repos, Inbox items, knowledge, catalog data, and configuration. Members can inspect. Admins can change settings and close decisions.

The assistant does not replace ownership. It helps retrieve context, draft answers, and show the next action.

## Technical mapping

The product vocabulary above maps to technical pieces engineers may see:

| Product word | Technical layer |
| --- | --- |
| Workspace | `/v1/workspaces`, members, policies, integrations |
| Connected repo | repo activation, GitHub App, repo secrets, bundle version |
| Inbox | clarifications, improvements, notifications, routing rules |
| Knowledge | buckets, articles, distiller, repo mirrored files |
| Automation | routines, generated workflows, pipeline records |
| Evidence | tracker comments, PRs, checks, audit events, callback payloads |
| CLI reference | `.ship/config.yml`, cache, pins, agent rule install targets |

Keep this mapping in implementation docs. Product-facing pages should stay with the left column unless a user is actively configuring the repo.

## Where to next

Read [Getting started](/getting-started) for setup order, [Knowledge](./knowledge-buckets.md) for context management, [Automations](./automations.md) for repeatable work, [Operating](./operating.md) for day-to-day review, and [Configuration](./configuration.md) for the repo-level reference.
