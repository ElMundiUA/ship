---
title: The repo refactor that gave shape to everything else
slug: the-shape-refactor
date: 2026-04-11
kicker: Architecture
description: Three top-level folders — documentation, prompts, runtime. Each one had a different reader. Renaming and moving files for half a day made the next four weeks possible.
reading_time: 4
author: Denys Kuzin
tags: [architecture, refactor, build-in-public]
---

Day five, after the extraction had shipped and the deploy pipeline had stopped catching fire, we did a refactor that doesn't sound like much. The commit body says *refactor: repo layout (documentation, prompts, runtime) + agent adoption*. Half a day of moving files around. No new behaviour.

It is the single most important commit of April for what came later.

## The shape before

Inside elmundi, the methodology had grown organically. There was a `docs/` folder for the manual, a `tools/linear-agent/` folder that contained both prompts and runtime code, a `prompts/` folder that contained *some* prompts, and a few scattered markdown files at the root that nobody could justify but nobody wanted to delete.

This was fine when one team owned the whole repo. It became indefensible the moment the methodology had to be a *product*, with different people maintaining different layers.

## The shape after

Three top-level folders. Each one has one reader.

**`documentation/`** is for humans. The manual. Rendered to `/docs/`. Prose, examples, diagrams. Reviewed for clarity.

**`prompts/`** is for agents. Role definitions, exit contracts, in-context examples. Rendered through the methodology API. Reviewed for *operational correctness* — does another agent execute this prompt sensibly?

**`runtime/`** is the code that runs. Reviewed for correctness. (Later renamed to `cli/` and `backend/` as those split, but on April 11 they were one folder.)

Plus two adjuncts:

- `landing/` — the Next.js app rendering the manual + the marketing site
- `artifacts/` — pattern/tool/collection definitions (this folder didn't fully separate from `prompts/` until RFC-0005, eight days later)

## Why it mattered

Three things became possible the next day that hadn't been possible the previous day.

**A pull request could declare its target reader.** Before the refactor, a PR could touch a prompt that was also a doc that was also a runtime config. Reviewers had to context-switch through all three readerships. After the refactor, a PR was either a docs change or a prompt change or a runtime change, and reviewers could optimize for one reader.

**The `methodology API` became sketchable.** With prompts in their own folder, the obvious move was a single endpoint that read from that folder and served them as a catalog. The `Unify methodology API for catalogs` commit came four days later and was a small change because the foundation was already laid.

**The agent-adoption rule shipped.** The same commit added the rule "agents must read from `prompts/` *via the API*, never from disk." That rule survives today and is what eventually became RFC-0005's hard separation between filesystem and runtime.

## What didn't change

The methodology. Not one prompt got rewritten. Not one rule got loosened. Everything that worked before kept working. This was *just* a refactor — same behaviour, different shape.

That's how I know it was the right refactor: behaviour identical, but four weeks of subsequent work all referenced "now that things live in folder X."

## The lesson

Naming folders is naming readers. If your folders don't correspond to who reads them, every PR has a renaming fight, every doc has the wrong audience, and every product decision flattens into a pile of "yes-but-also" arguments.

When the readers are clear, the schemas follow. When the schemas follow, the contracts get tight. When the contracts get tight, the product can ship.

Three folders. Half a day. Everything later.
