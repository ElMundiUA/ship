---
title: The methodology API — one endpoint, two consumers
slug: methodology-api-one-endpoint
date: 2026-04-15
kicker: Architecture
description: shipctl reads from it. Every agent reads from it. The customer's repo never sees the source files. One HTTP API, deliberately small, cleanly versioned. The shape that made the rest of April land smoothly.
reading_time: 4
author: Denys Kuzin
tags: [architecture, api, build-in-public]
---

By April 15 the repo had a refactor (`documentation/`, `prompts/`, `runtime/`), an Apache-2.0 license, a CLI scaffold, a deploy pipeline, and several adapters. What it didn't have was an *API*. The methodology was still being read off disk by everything that needed it.

The commit that fixed this was `Unify methodology API for catalogs and add npm publish workflow`. One endpoint shape. Two consumers: `shipctl` and the in-process agent runtime. The customer's repo never reads the source files directly.

This is a short note about *why* one endpoint and *why* two consumers, in a stack where it would have been easy to skip the API and have everything read the filesystem.

## What the API does

Three resource families, two verbs each:

- `GET /patterns`, `GET /patterns/:id`
- `GET /tools`, `GET /tools/:id`
- `GET /collections`, `GET /collections/:id`

Plus a fetch endpoint:

- `POST /fetch` with `{kind, id, version}` returns the body

That's the whole shape. No mutations. No filtering language. No GraphQL. The endpoints are not even particularly RESTful — they're a thin shell over a deterministic catalog.

The catalog itself is built from the on-disk source files (`prompts/`, later `artifacts/`) at server start, with a checksum so we know when it's stale. A pattern at v0.4.2 returns the same body forever; a new body means a new version.

## Why one endpoint

I argued for a richer API at first. *Surely the agent will want filters? Surely the CLI will want pagination?* No, and no.

`shipctl` lists everything once and caches locally. The agent fetches one artifact at a time, by ID + version. Neither consumer needed filters. Neither consumer needed pagination. We were planning around imagined needs.

When we wrote the actual consumer code, the API stayed small. **The smallness is the feature.** A small API is one we can keep stable for the lifetime of the project. A clever API is one we'd have to deprecate in three months.

## Why two consumers, both via HTTP

`shipctl` is a separate process running on the customer's machine. Of course it goes through HTTP. That's not the interesting part.

The interesting part is that the *in-process agent* — code running in the same Python process as the catalog server — also goes through HTTP. It would be technically faster to read from disk directly. We chose not to.

The reason is simple: **the in-process agent is the canonical consumer**. If we let it read from disk, we'd be telling everyone else (`shipctl`, future external integrators, the customer's own scripts) that they're a second-class citizen. They'd hit edge cases the in-process code never hit. Bugs would split between paths.

By forcing the in-process agent through HTTP, we guarantee that *what shipctl sees is what the agent sees*. There's exactly one read path. If it has a bug, both consumers find it on the same day.

## What this enabled

A bunch of things, in order of how often they came up:

- Versioning. Bumping a pattern from v0.4.2 to v0.4.3 means the API returns the new version next time someone asks for `latest`, but agents already in flight still get v0.4.2 by ID. No coordination needed.
- Cacheability. `shipctl` caches by ID+version. The cache is correct because versions never change. We can be aggressive about cache duration.
- Auditability. Every agent run records which artifact ID+version it read. The methodology API is the source of truth for "what did the agent know."
- The MCP-server experiment didn't ship. The decision to stick with this API instead of running an MCP server in parallel was clean precisely because the API was already working.

## The lesson

A small API is a stable API. Two consumers on the same HTTP path is one fewer surprise per week. The temptation to make the in-process consumer "fast" by giving it filesystem access is the temptation that produces split codepaths and inconsistent bugs.

We made the in-process consumer go over HTTP and we never looked back. Every other piece of architecture in April landed smoothly because of it.
