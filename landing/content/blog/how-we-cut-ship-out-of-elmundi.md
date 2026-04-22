---
title: How we cut Ship out of elmundi
slug: how-we-cut-ship-out-of-elmundi
date: 2026-04-07
kicker: Origin
description: Ship did not begin as a greenfield repo. It began as a folder inside a product called elmundi. On Apr 7 we cut it out, and twenty commits later it was a standalone thing with its own CI, its own docs, and its own CLI. This is the prequel to every other post on this blog.
reading_time: 6
author: Denys Kuzin
tags: [origin, build-in-public, autopsy]
---

Ship did not begin as a greenfield repo. It began as a folder inside a product called elmundi, where we had been running coding agents against a real codebase for months. The methodology worked. The fact that it lived inside somebody else's app did not.

On Apr 7 we cut it out.

This is the prequel to every other post on this blog — the day the methodology stopped being a habit and started being a product. Twenty commits, one extraction, a LICENSE file, and enough CI to get `mkdocs build` on push. We are writing it down because the shape of day one is the shape we kept returning to.

## What "extracted from elmundi" actually means

We had been running a coding-agent delivery loop inside elmundi for about six months. The loop worked: Linear tickets in, GitHub PRs out, roles defined as prompt artifacts, cadences (daily retro, PR self-review, tech-debt scan) defined as scheduled workflows. The artifacts lived under `tools/linear-agent/` and `prompts/`. The runtime was a bag of Node scripts, some bash glue, and an MkDocs site that served as both the operator manual and the agent's own reading list.

None of that was Ship yet. It was elmundi using Ship.

The first commit of the new repo says this plainly: `Initial import: Ship framework (extracted from elmundi)`. A single commit that carried over the manual, the prompts, the Node runtime, and the scripts — with paths rewritten, elmundi-specific examples gone, and a LICENSE file where there hadn't been one before. The commit body notes that docs and `mkdocs.yml` point at `ElMundiUA/ship` and that the ElMundi examples chapter will mirror paths "until submodule/pin." That "until" is important. It is the first line of the handoff.

> A methodology that only works inside the repo that grew it isn't a methodology. It's a habit.

We had a working habit. We wanted a methodology. That is the entire argument for pulling it out.

## Twenty commits is the price of day one

After the extraction commit, the log goes like this, in order:

- `ci: Docker image for docs + Bunny Magic Containers deploy`
- `ci(bunny): auto-provision Magic Container app + DNS summary`
- `ci: clarify Bunny workflow, explicit MC deploy, PR-only docs`
- `fix(bunny): safer MC create payload (name, regions, sticky cleanup, 500 retry)`
- `fix(bunny): strip MC create payload to OpenAPI-allowed fields`
- `fix(bunny): create MC app without config-suggestions, region/probe fallbacks`
- `docs(bunny): clarify MC POST /apps 500 — manual app or BUNNY_APP_ID`
- `fix(bunny): honor BUNNY_APP_ID over name lookup; clarify vars in README`
- `chore(prompts): translate cloud-prompts and related templates to English`
- `fix(cloud-prompts): correct branch markdown in _base; restore EOF newlines`
- `chore(docs): bump documentation version to 0.6.0`
- `fix(ci): resolve Bunny MC container name; generalize Ship docs and tooling`
- `ci(bunny): deploy Magic Containers with sha-* image tag, not latest`
- `fix(ci): await Bunny PATCH with image digest; replace fire-and-forget action`
- `fix(ci): PATCH Bunny with Hub-fetched digest + imagePullPolicy always`
- `feat(cli): ship-agent multi-tracker adapters and docs`
- `refactor: repo layout (documentation, prompts, runtime) + agent adoption`
- `docs: expand framework chapters; add adopt-ship.sh launcher`
- `fix(docs): include prompts/ in Docker build for MkDocs snippets`

Nineteen commits after the import. Twenty for the day.

The shape is what surprised us in hindsight. Twelve of those commits are infrastructure — specifically, wrestling with Bunny Magic Containers, the host we picked for the docs site. Three are docs cleanup (version bump, chapter expansion, an `adopt-ship.sh` launcher). Two are prompt cleanup (translate to English, fix trailing newlines). One is the first pass of a CLI that would be rewritten from scratch a week later. One is a repo-layout refactor — the one that moved MkDocs source into `documentation/`, created `prompts/cloud-agent/` and `prompts/catalog/` and `prompts/onboarding/`, and made the `runtime/` folder a proper Node workspace.

{{chart: blog-extraction-mix | Twenty commits on Apr 7 by theme. Half of the day was spent on Bunny Magic Containers — the wrapper around the docs — not on anything that would survive to v0.9. }}

The infrastructure commits did not survive. The MkDocs pipeline they prop up was retired five days later (we write about that in _The protocol before the product_), and the Bunny workflow those twelve commits were fighting has been rewritten twice since. At the time it felt like we were building foundations. In retrospect we were paying the tenant-to-landlord transition fee.

## The tenant-to-landlord fee

This is the part worth naming.

When you extract a methodology from a host repo, the host was doing things for you that you did not know you depended on. In elmundi's case: CI that built the docs site, a domain and deploy target for the operator manual, secrets for the container host, a LICENSE, a README that described the product the methodology was sitting inside of. Every one of those had to be reacquired by the new repo, on day one, before anyone other than us could read it.

That is the twelve-commit fight with Bunny. Not because Bunny is hard — it is not, it is actually one of the simpler container hosts — but because we were doing it for the first time in a repo that was meant to own it. Each of those commits is a specific thing we got wrong and then got right: a payload field the OpenAPI spec actually rejected, a DNS step that needed an `BUNNY_APP_ID` env var instead of a name lookup, an image tag that needed to be `sha-*` and not `latest`, a `PATCH` that was being awaited in the wrong way.

We could have avoided a lot of this by just shipping the docs as a static site on GitHub Pages. We chose not to. We wanted the deploy story of our product to be a real product deploy story, not a convenience target, because every later thing we would build — the landing site, the cloud console, the backend — was going to need the same muscles. Paying that bill on day one made the same bill cheaper on day twelve.

> Twelve commits is what it costs to turn a habit into a product.

## The CLI that didn't survive

One of those twenty commits is `feat(cli): ship-agent multi-tracker adapters and docs`. The subject line reads like a real feature. It renames the npm package to `ship-agent`, adds a `linear-agent` alias, creates a `TrackerFacade` with Linear, Jira Cloud, GitHub Issues, Azure Boards, and ClickUp adapters, and documents the config surface.

None of this is still in the code. On Apr 18 the CLI was rewritten from scratch and renamed from `ship-agent` to `shipctl`. The `TrackerFacade` from Apr 7 was thrown away and replaced by a different adapter interface built against a protocol that didn't exist yet on Apr 7. The only thing from this commit that survived is the idea — "Ship is a binary you type into, it reads a config, it talks to a tracker, it does something useful, it can be pointed at different trackers without knowing anything about them" — and even that is a shape the rewrite kept, not code.

This was the right call. The Apr 7 CLI was a port of the scripts we had inside elmundi. The Apr 18 CLI was the first CLI built on top of a protocol. The two are not the same kind of thing and should not have lived in the same version.

On day one you do not know that yet. On day one you port what you have and hope it survives. Eleven of your twenty commits will not. That is fine. The point of day one is that the thing compiles and the CI passes. The point of week two is that the thing was right.

## The refactor that did survive

`refactor: repo layout (documentation, prompts, runtime) + agent adoption` is the one commit from Apr 7 whose shape is still visible today. It moved the MkDocs source into `documentation/`, it split prompts into `cloud-agent/` (CI-executed prompts), `catalog/` (the A-series), and `onboarding/` (adopt playbooks), and it made `runtime/` a proper Node workspace with a single root lockfile.

That folder structure is still there, with corrections. The `prompts/` top-level directory got renamed again later when RFC-0005 consolidated everything into `artifacts/patterns/` and retired the `catalog-a*` family entirely — but the principle of "one repo, documented layer / runnable layer / methodology layer" held. The reason it held is that the refactor was describing a product, not an elmundi export. The commit message says `(documentation, prompts, runtime) + agent adoption`. Adoption was the second word because the first word was product.

If you look at the day through the lens of "which commits will I still be defending in two weeks," this is the one.

## What we chose not to do

We chose not to ship a landing site on day one. The docs site was enough; an actual marketing landing would come on Apr 12 and get thrown away and re-cut several times.

We chose not to publish to npm on day one. The CLI got packaged but not published; publishing took another nine days and a 2FA-bypass CI token to get right.

We chose not to port the telemetry code from elmundi, even though it worked. The Ship version of telemetry was going to need to be opt-in and operator-controlled from the start — not because the elmundi code was wrong, but because the contract between a methodology and the consumer using it is different from the contract between a service and its own operator. We wanted to write that contract on a clean page. RFC-0003 landed eleven days later.

We chose not to make any claims about the product. No landing page, no marketing copy, no teaser on social. The first public mention of Ship in our channels was on Apr 19, twelve days later, when there was actually a console a human could click on.

Each of those was a "not yet." None were "never." But on day one the list of things you are not shipping matters as much as the list you are.

> You can tell a product has decided what it is when its day-one backlog has both columns.

## What day one earned

Twenty commits. One repo. A docs site that built and deployed through CI we owned. A repo layout that outlived the CLI that sat inside it. A LICENSE file. A README that described Ship and not the product Ship used to live inside.

Nothing visible to a user. No landing page. No cloud. No binary on anyone's PATH. That was fine. The point of day one was that there was now a repo Ship could be developed in.

By the end of the week that repo would have a landing site, a published CLI, and a backend. By the end of the second week it would have a cloud console, a book, and a pilot install running end-to-end on a customer repo. None of that was possible on Apr 7. All of it was made possible by Apr 7.

Extraction was cheaper than embedding. Not in commits. In scope. An embedded methodology is a method you and your team know how to run. An extracted methodology is a method anyone can run, which means it has to answer questions its former host never had to — about docs, about publish, about adoption, about the shape of its own install.

The twenty commits of Apr 7 are what it cost to start answering those questions. We are going to keep writing this blog for as long as the answers keep changing.
