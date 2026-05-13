---
slug: 2026-04-19-ship-cut-out-of-elmundi
date: 2026-04-19
title: Ship cut out of elmundi
summary: Day zero. The methodology lived inside a host product for six months. We extracted it into its own repo, gave it a license, a CLI, a landing site, and a deploy. Twenty commits to get there.
kicker: Bootstrap
prs: []
---

Ship didn't begin as a greenfield repo. It began as a folder inside a product called elmundi where coding agents had been running against a real codebase for months. The methodology worked. The fact that it lived inside someone else's app didn't.

This is the entry for the first thirteen days — the period before the PR-flow started — read off the actual git log. It's not a long entry because the work was foundational rather than user-facing: a repo, a license, a CLI, a deploy, and a landing app where there hadn't been anything before. There's a longer telling on the [Ship Log](/blog/how-we-cut-ship-out-of-elmundi).

## Highlights

### The extraction

The first commit on the new repo says it plainly: *Initial import: Ship framework (extracted from elmundi)*. A single commit that carried over the manual, the prompts, the Node runtime, and the scripts — with paths rewritten, ElMundi-specific examples gone, and a LICENSE file where there hadn't been one before.

### shipctl v0.9 — protocol before product

Eleven days after the extraction, on a Sunday, `shipctl v0.9` landed: artifacts protocol, stack adapters, pharma pilot e2e. The CLI shipped before the cloud product because the *contract* between client and server is what makes Ship plural. Without that contract, every adopter would fork the runtime; with it, every adopter speaks to the same protocol.

### Landing app + retire MkDocs

MkDocs was the original docs runtime. This week it got retired in favour of a Next.js landing app under `landing/` — same content, different runtime, with a downloadable book PDF and a real `/docs` viewer. The docs MCP server experiment got removed (kept the lesson, dropped the code).

### RFC-0005 — artifacts as frontmatter

The first piece of public RFC-driven cleanup. Two waves: 61 artifacts moved to a v2 folder layout (Wave 1), then the legacy manifests got dropped and the filesystem became the single source of truth (Wave 2). Fewer concepts, fewer places where the same artifact was described, one less footgun. The longer telling lives at [/blog/artifacts-are-frontmatter-now](/blog/artifacts-are-frontmatter-now).

### Bunny Magic Containers deploy

CI got a real deploy target. Bunny Magic Containers, EU region, with auto-provisioned app + DNS summary. Several follow-up fixes through the week to nail down the create/PATCH flow and make it idempotent.

## Improvements

- `cli:` `ship search` + resource commands; docs-only `fetch` / `feedback`
- `cli:` ship-agent multi-tracker adapters + docs
- `cli:` published as `@elmundi/ship-cli` under npm org `elmundi`
- `landing:` downloadable `book.pdf` + Download CTA on `/book`
- `docs:` book — Prologue, Manifesto, 9 lettered sub-chapters, 8 field notes
- `docs:` Ukrainian Part II — Prompts & workflows
- `docs:` expand framework chapters; add `adopt-ship.sh` launcher
- `prompts:` translate cloud-prompts and templates to English
- `bunny:` deploy Magic Containers with `sha-*` image tag, not `latest`
- `bunny:` PATCH with Hub-fetched digest + `imagePullPolicy: always`

## Fixes

- `bunny:` honor `BUNNY_APP_ID` over name lookup; clarify env vars in README
- `bunny:` strip MC create payload to OpenAPI-allowed fields
- `bunny:` safer MC create payload (name, regions, sticky cleanup, 500 retry)
- `bunny:` create MC app without config-suggestions; region/probe fallbacks
- `docker:` workspace lockfiles + dev deps for Next build
- `bunny:` align container port 8080 with Magic Containers template
- `docs:` include `prompts/` in Docker build for MkDocs snippets
- `cloud-prompts:` correct branch markdown in `_base`; restore EOF newlines
- `ci:` PATCH Bunny with image digest; replace fire-and-forget action
- `ci:` resolve Bunny MC container name; generalize Ship docs and tooling
- `ci:` publish `@ship/cli` workspace with `npm -w`, not `--prefix`
