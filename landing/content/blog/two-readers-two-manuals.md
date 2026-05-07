---
title: Two readers, two manuals
slug: two-readers-two-manuals
date: 2026-05-08
kicker: Best practice
description: Documentation written for a human and documentation written for an agent are different artifacts. We learned that the hard way and split them. Here is what changed when we did.
reading_time: 7
author: Denys Kuzin
tags: [docs, agents, methodology, build-in-public]
---

Ship has two folders that look like documentation. They are not the same kind of thing.

`documentation/` is for humans. Its render is `/docs/` on the live site. Prose, examples, diagrams, the reasoning behind a decision, and the occasional self-deprecating aside. A senior engineer reading it should be able to put a workspace together in an afternoon.

`artifacts/` is for agents. Its render is not a website at all — it is a JSON catalog served from `/patterns`, `/tools`, `/collections`, fetched by `shipctl` and by every role agent that runs inside Ship. There is no narrative. There is frontmatter, IDs, paths, schemas, and a body that has been peer-reviewed by a different set of eyes — *another agent*.

For three weeks we tried to keep both readerships happy with one set of files. It cost us every week.

## The mistake

When you ship code that runs inside an LLM-driven agent loop, your first instinct is "the agent should just read our docs." Of course. Docs are written. Markdown is structured-ish. The agent has retrieval. We're done.

Except the agent isn't reading docs. The agent is *acting on* them.

A human reading "we usually use Postgres" thinks, *OK, you usually use Postgres*. An agent reading the same line thinks *the contract says Postgres* and rejects a config that uses MySQL — even though the doc says "usually," not "must." The hedge that signals nuance to a human is invisible to a model with high confidence and no eyebrow.

This is the central asymmetry. **Prose hedges are a feature in human-readable text and a bug in agent-readable text.** A model trained to be helpful will treat your aside notes as instructions. It does not know they are asides.

Going the other way is worse. A human opening a stripped-down YAML schema with no examples, no reasoning, no aside notes — bounces in thirty seconds. They don't know what to do with `transition.trigger_actor: user | agent | either`. They want a paragraph that says *what* the field is for and *why* the three values, please.

So: one folder where prose is the feature. Another folder where prose is the bug. Same word "documentation" — completely different artifacts.

## What we changed

Three structural moves, none of them subtle.

**Split the folders.** `documentation/` is human. `artifacts/` is agent. Different schemas, different review cycles, different render targets. RFC-0005 collapsed each artifact into a single file with strict frontmatter and a body the agent retrieves; nothing else lives there. The story of that collapse is a separate post — *[Artifacts are frontmatter now](/blog/artifacts-are-frontmatter-now)* — but the *why* is here: an artifact body cannot afford prose that drifts from the schema, so it does not get prose at all.

**Different review eyes.** A pattern's body in `artifacts/` is reviewed by another agent before merge. We literally have a routine that runs the artifact through a "would another role agent operate from this body in production" check. If the body reads like a blog post, the check fails. If the frontmatter is missing fields, the check fails. The reviewer is not a human; the reviewer is an LLM being asked to operate from this exact body, on a real ticket, with the same workspace tools the production agent has.

**Catalog API as the moat.** Agents do not read `artifacts/` from the filesystem. They read it through `GET /patterns`, `/tools`, `/collections` — versioned, immutable per version, addressable by ID. If we change a pattern body without bumping the version, an agent in flight cannot accidentally pick up half-old, half-new content. Humans can edit the manual and ship the next render in five minutes. Agents see exactly the version they were given when their run started, for the lifetime of that run.

These three things — folders, reviewers, API — turned "documentation" from one process into two. Both more demanding than what existed before. Both *much* clearer about who they are for.

## What humans get

Walk into `documentation/`, or its render `/docs/`:

- **Narrative.** *The Inbox is not a backlog* opens with a one-sentence promise and unwraps it for two paragraphs before the schema appears. That isn't slow; it's loading the human's working memory with a frame.
- **Examples.** Every concept has at least one worked example. A senior engineer reading should be able to ctrl-F for their case.
- **Reasoning.** Why we chose a seven-state canonical lifecycle, not four or twelve. Why `awaiting_input` is *not* `blocked`. The reasoning is on the page; the consequence is the schema.
- **Diagrams.** Most pages have one. The SVG generator is in the repo, but the diagrams exist for the human eye. If you remove them, the page still parses; it just costs the reader an extra minute.
- **Reading time labels.** Three minutes vs eleven minutes. We tell you upfront; we don't pretend everything is a thirty-second skim.
- **The occasional joke.** Not many. But Ship has a voice; the docs carry it. *You can't `git push --force` reality.*

What humans do NOT get from `documentation/`:

- Strict schemas as the primary content
- Field-by-field tables that would be unreadable in prose
- Agent-addressable IDs with no other context

For those, the docs link out to the catalog endpoints — which exist precisely because they are not in this folder.

## What agents get

Walk into `artifacts/`, or its render via `/patterns/{id}`:

- **Frontmatter as contract.** Every field is required, validated, versioned. Missing field = build fails, not "renders weird."
- **Stable IDs.** `pattern.intake-clarification` does not become `pattern.intake-clarify-questions` next month. If we want to rename, we deprecate first; the old ID resolves to the new for one minor version.
- **Strict schemas.** `transition.trigger_actor: user | agent | either`. No fourth option. No "we sometimes do X." The schema is the answer.
- **Versioning.** A pattern at v0.4.2 stays at v0.4.2 forever. New body? New version. The agent that reads v0.4.2 today gets the same thing tomorrow, and in production six months from now.
- **No prose.** The body of an artifact is steps, not story. If you find yourself writing "for example…" in a pattern body, that line is in the wrong file. It belongs in `documentation/`.
- **Citation paths.** Every claim an agent makes traces back to an artifact ID + version. *I picked the QA-architect role because `pattern.test-architecture` v0.7.1 says…* This is the audit trail; this is what makes the difference between "an agent did something" and "evidence beats opinion."

What agents do NOT get from `artifacts/`:

- Long-form reasoning ("we chose this because…")
- Examples written for humans to learn from
- Hedges, asides, jokes

If a pattern body needs to express a fallback, it gets a *new field* — `alternatives`, `escape_hatches` — not a paragraph of prose. The schema absorbs the nuance the prose used to carry. That is harder than writing the paragraph. It is also why the agent can be trusted to follow it.

## The cost of mixing

Before the split, we had one set of pattern files and we tried to write them so both readers could use them. Three failure modes, in order of how often they bit us:

**The agent over-trusted prose.** A pattern said *we usually do X but if Y, do Z*. The agent ran the pattern, hit case Y, and proceeded with X anyway because the example showed X. The hedge was invisible. We caught this in production once. Once was enough.

**The human couldn't find the contract.** Engineering onboarding: "what does this pattern actually require?" The answer was buried in a paragraph that started with *this is generally about…* Three quarters of an hour to find the schema, and a senior engineer who left the page muttering.

**The two writers fought.** When the same file is being optimized for both readers, every PR has a fight: more prose for the human (the agent will mishandle hedges) or more schema for the agent (the human will bounce on the wall of YAML). Compromise documentation is bad documentation, in both directions.

After the split, each PR optimizes for one reader. The pattern PR strips fluff. The doc PR adds it back. Both files improve. The two readers stop competing.

## A note on the third reader

Ship actually has a third documentation surface: `landing/content/book.md`. That one is written for a different human — not the engineer onboarding, but the person deciding whether to *adopt* this whole way of working. It is more philosophy than schema. Different folder, different render, different reader.

The principle is the same. **Pick the reader before you pick the words.** Books are not docs are not artifacts. Trying to make any one of those three carry the weight of the other two is the same category error every time.

## The lesson

If you are building a product where AI agents act on your codebase, you have at least two reader bases. They want different things. They cannot share a manual.

**Humans want narrative. Agents want contracts. One cannot be the other.**

The temptation to use one source — *DRY, single source of truth, write once* — is the strongest mistake you can make in this stack. It is a category error: you are treating *documentation* as a single artifact when it is two artifacts that live in two folders, get reviewed by two different reviewers, render to two different surfaces, and get versioned on two different cadences.

Ship's `documentation/` and `artifacts/` are not "old docs and new docs" or "the wiki and the API reference." They are **the two manuals every AI-native codebase needs**.

Pick which file you are writing. Then write it for that reader. The other file is somebody else's problem.
