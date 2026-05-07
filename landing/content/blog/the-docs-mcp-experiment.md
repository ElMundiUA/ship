---
title: The docs-mcp-server experiment we deleted
slug: the-docs-mcp-experiment
date: 2026-04-12
kicker: Autopsy
description: We built a Model Context Protocol server to expose the docs to agents. It worked. Then we deleted it. Why building the right thing the wrong way is sometimes worse than not building it.
reading_time: 4
author: Denys Kuzin
tags: [autopsy, mcp, agents, build-in-public]
---

Two commits, three days apart:

- `feat: add initial implementation of docs-mcp-server with Docker support`
- `chore(repo): remove unused docs-mcp-server experiment from /tools/`

In between: about forty hours of work and a lesson we hadn't expected.

## What it was

The pitch was clean. Anthropic's Model Context Protocol gives agents a uniform way to call into external tooling. We have docs the agent needs to read. Therefore: an MCP server that wraps `documentation/` and exposes it as searchable tools.

We built it. It started, accepted MCP requests, and returned doc passages. The Docker image was 80 MB. The protocol round-trips were single-digit milliseconds. The whole thing felt clean.

## Why we deleted it

Three reasons, in order of how loudly they argued.

**The agent didn't need MCP — it needed the catalog API.** When an agent asks "what's the role definition for QA-architect," it doesn't want to retrieve a markdown passage and parse it. It wants the *structured artifact*: ID, version, body, schemas. We were already building that as the methodology API (`/patterns`, `/tools`, `/collections`). MCP gave us a protocol; what the agent needed was a *contract*. Different things.

**Two read paths is one too many.** With both MCP and the methodology API live, the agent had to decide: which one for which question? That decision is exactly the kind of thing LLMs get wrong. We didn't want to spend any optimization budget teaching the agent to pick its read path; we wanted a single read path that always worked.

**The MCP server's killer feature was the wrong feature for our case.** MCP shines when many independent agents need to call into many independent tools. We have one workspace, one agent ecosystem, one source of docs. The polyglot interop that MCP buys you is a payment for a problem we don't have.

## What we learned

- **MCP is great for the right shape of problem.** If you're building a tool that *external agents* will integrate with — give them an MCP surface. They'll thank you.
- **MCP is the wrong shape for "my agents read my docs."** That's a methodology API. Versioned, typed, single-source.
- **The cost of an extra read path is hidden.** It looked like the MCP server cost us forty hours up front. The real cost was the *steady-state* cognitive overhead of two ways to ask the same question. We caught it on day three and walked it back.

## The deletion commit

The body of the deletion commit is one line: *no users, no consumers, no follow-up planned*. We deleted the Docker config in the same commit. Removed the `tools/docs-mcp-server/` folder, removed the workflow that built it, removed the README that explained it. Net diff: -1,247 lines.

I keep that commit on a sticky note. It was a real piece of work, well-built, and walking it back was the right move. We were not "wrong to try." We were right to *stop*.

## The lesson

Building the right thing the wrong way is sometimes worse than not building it. Two read paths is one too many. The agent did not need MCP; it needed contracts.

We deleted forty hours of work and shipped faster afterwards because of it. That arithmetic is not intuitive. It's correct.
