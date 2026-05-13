---
title: Killed MkDocs, kept the URLs
slug: killed-mkdocs-kept-the-urls
date: 2026-04-10
kicker: Architecture
description: We replaced the docs runtime in one commit. Same content, same URL paths, different stack. The constraint that did most of the work was "no broken links."
reading_time: 4
author: Denys Kuzin
tags: [architecture, docs, build-in-public]
---

On April 10 the docs runtime changed. MkDocs out, Next.js in. Same Markdown source files, same URL paths, different rendering. The commit is one line in the git log: *feat: landing app, ship CLI, backend API; retire MkDocs runtime.*

The interesting part was the constraint. "No broken links" forced every other decision.

## Why retire MkDocs

MkDocs was fine. It still is fine. We retired it for two reasons that had nothing to do with MkDocs itself:

**One render target, multiple audiences.** The same site needed to host the docs (`/docs`), the marketing pages (`/`, `/use-cases`, `/process`), the book (`/book`), and the catalog pages (`/patterns`, `/tools`, `/collections`). MkDocs is excellent at the first one, awkward at the others. Running two stacks side by side — MkDocs at `/docs` and Next at the rest — was a deploy story we didn't want to maintain.

**Real interactivity for the marketing surfaces.** The hero needs to render a process mock with live ticket-card animations. The roadmap teaser needs to pull from the live `/v1/roadmap` endpoint at build time. None of that fits MkDocs without bolt-ons.

## The constraint

Here's what made the migration tractable: **every existing `/docs/...` URL had to keep working**.

Inside elmundi, the docs URL had been linked from chat messages, from PR descriptions, from agent outputs that other agents had cited. Breaking those would have created a tail of broken citations across the methodology system itself. So the rule was simple: same path on the new site, same content, same anchor IDs.

That constraint did most of the work. It meant we couldn't reorganize the docs hierarchy "while we were in there"; the migration had to be **render-only**, not structure. Which meant the migration could land in one commit instead of fifteen.

## What changed

- Markdown source files moved from the MkDocs working tree to `documentation/` at the repo root.
- The Next.js `landing` app added a dynamic route `/docs/[...slug]` that reads `documentation/**.md`.
- The MkDocs `mkdocs.yml` got deleted in the same commit. No "deprecated, will remove next month" period — the new render shipped, the old one was gone.
- A second commit later in the day fixed `Docker build for MkDocs snippets` (we still bundled MkDocs *content* into the Docker image for one more day, until the consumers got pointed at the new render).

## What didn't change

Anchor IDs. Each H2 in the source had a slug that MkDocs auto-generated; we matched it in the new render. Bookmarks didn't break.

URL trailing slashes. MkDocs served `/docs/foo/`; Next.js redirected `/docs/foo/` → `/docs/foo`. We added the redirect explicitly so old links worked.

Markdown extensions. The source uses MkDocs admonition syntax (`!!! note`); the new render preserves it via a remark plugin.

## The lesson

When you replace a runtime, the URLs are the contract. The content is *implementation*; the URLs are *interface*. We migrated the runtime in a day because we held the interface constant.

The reverse — keeping the runtime, changing the URLs — would have been catastrophic, because every external citation would need rewriting, and we control none of those.

The shape of the migration generalizes: **whatever the smallest stable contract is, hold it constant**, and everything inside can change in a single commit.
