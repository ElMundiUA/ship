# Manual

The Manual explains how to set up and operate Ship as a product delivery workspace. It starts from the work a product owner, lead, or platform team actually needs to see: connected repos, tracker-backed intent, decisions that need attention, knowledge agents can use, and evidence that survives review.

It is not the book, the catalog, or the CLI flag list. The book explains why the method exists. The catalog lists reusable procedures and integrations. The CLI page is the developer workbench.

## The public vocabulary

Use these words in user-facing docs and landing copy:

- **Workspace** — the team or product area Ship operates inside.
- **Connected repo** — a repository Ship can observe and help keep wired.
- **Tracker** — the system of record for product intent, ownership, blockers, and done.
- **Inbox** — the place for clarifications, improvements, approvals, failures, and exceptions that need a human decision.
- **Knowledge** — product facts, repo context, policies, and standing rules available to agents and reviewers.
- **Automation** — a repeatable check or agent-assisted action that runs under explicit rules.
- **Evidence** — links, comments, pull requests, checks, and knowledge updates that explain what happened.

Keep protocol terms such as `lanes`, `pipeline_runs`, `RFC-*`, `shipctl init`, and `.ship/config.yml` out of first-read pages unless the page is technical reference.

## What you will find here

- **[Product concepts](./concepts.md)** — workspace, repo, tracker, Inbox, knowledge, automations, evidence, and the book postulates behind them.
- **[Automations](./automations.md)** — how repeatable work stays bounded, human-owned, and auditable.
- **[Knowledge](./knowledge-buckets.md)** — how product and repo context reaches agents without turning prompts into a private wiki.
- **[Operating](./operating.md)** — how to review blockers, shipped work, decisions, and evidence after setup.
- **[Configuration](./configuration.md)** — the technical reference for `.ship/`, `shipctl`, config fields, and versioned artifacts.
- **[Agent matrix](./agent-matrix.md)** — supported agent ids and on-disk rule targets.
- **[Authoring artifacts](./authoring.md)** — how to write a pattern, tool, collection, preset, or adapter.
- **[Discovery contract](./discovery.md)** — the structured interview agents use before their first meaningful change.
- **[Protocol](./protocol/index.md)** — implementation specs and RFCs.
- **[Troubleshooting](./troubleshooting.md)** — symptom-first fixes for console, repo setup, knowledge, and CLI failures.
- **[Legal](./legal.md)** — license and versioning policy.
- **[Changelog](./CHANGELOG.md)** — changes to the Manual itself.

## What stays true from the book

The Manual uses simpler words than the book, but it keeps the same spine:

- Humans own intent. Ship can move work, but it does not own the product decision.
- Legibility is kindness. A future reviewer should understand what ran, why, and where the proof lives.
- Quiet systems beat loud demos. The goal is controlled delivery, not a fireworks show of agent activity.
- Evidence beats opinion. Each important action should point to a ticket, PR, check, comment, or knowledge article.
- Fences beat exhortations. Machines need clear allowed scopes, states, owners, and secrets.
- Vendors are plugs, not gods. Tracker, CI, model, and agent host can change while the story stays stable.

## Where to next

Start with [Getting started](/getting-started) for the product setup path. Then read [Concepts](./concepts.md), [Knowledge](./knowledge-buckets.md), and [Operating](./operating.md). Use [Configuration](./configuration.md), [Agent matrix](./agent-matrix.md), and the [CLI](./configuration.md#local-commands) when you are implementing or debugging the repo-level wiring.
