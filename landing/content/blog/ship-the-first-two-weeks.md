---
title: Ship — the first two weeks
slug: ship-the-first-two-weeks
date: 2026-04-22
kicker: Build in public
description: 189 commits. 16 days. One repo. The story of how Ship, shipctl, and the Ship Console went from an extracted folder to a running cloud platform — read off the actual git log.
reading_time: 11
author: Denys Kuzin
tags: [build-in-public, autopsy, commits]
---

> **Editorial note (2026-04 IA refresh).** This post predates the current
> CLI naming. `shipctl adopt` is not a real command; the equivalent today
> is `shipctl init` (or `shipctl new` for a greenfield repo). For the
> current command surface see [`cli/README.md`](https://github.com/ElMundiUA/ship/blob/main/cli/README.md).
> The narrative is preserved as a historical artefact.

Sixteen days. 189 commits. One repo.

That is the entire history of Ship as of this morning. We are publishing this on the sixteenth day because we promised ourselves we would, and because the log is finally interesting enough to read out loud.

> Ship shipped Ship in two weeks, and it showed.

We want to walk the log honestly — the bootstrap day, the quiet middle week, the Sunday the cloud console was born, the two back-to-back 53-commit days of pilot hardening, and the quiet Wednesday that came after. There is a shape to it, and the shape is the point.

## Apr 7: extraction day

Ship did not begin as a greenfield repo. It began as a folder inside a product called elmundi, where we had been running coding agents against a real codebase for months. The methodology worked. The fact that it lived inside somebody else's app did not.

So on Apr 7 we cut it out.

Twenty commits that day. The first one is the only one that matters for the story: `Initial import: Ship framework (extracted from elmundi)`. Everything after it is the kind of work you do when a tenant becomes a landlord — CI for our own container builds ("Bunny Magic Containers"), a Docker image for the docs, provisioning scripts, a repo layout refactor, the first pass of the `ship-agent` CLI with multi-tracker adapters, and a commit called `docs: expand framework chapters; add adopt-ship.sh launcher` that was us trying to convince ourselves people other than us could install it.

We could have left it embedded. It would have been cheaper for a while. But a methodology that only works inside the repo that grew it isn't a methodology, it's a habit.

Twenty commits is what it costs to turn a habit into a product on day one. We were fine paying that.

## The quiet week

Apr 8 through Apr 18 is the part of the graph the outside observer would skip. Two commits, four commits, one commit, five commits, one commit — nothing that looks like momentum. If you judged the project by daily commit velocity, you would close the tab.

{{chart: blog-commits-per-day | Commits per day, Apr 7 → Apr 22. The red spike is the day Ship Console was born. }}

We judge it differently. The quiet week is where the product actually decided what it was.

Three moments matter.

First, Apr 12. A single commit lands: `feat: landing app, ship CLI, backend API; retire MkDocs runtime`. We had been treating our methodology as a documentation site. That commit retires that assumption. The docs are no longer the product; they're an artifact the product consumes. That reframing is what makes every later decision possible.

Second, Apr 15 and Apr 16. `Unify methodology API for catalogs and add npm publish workflow` on Tuesday, then `feat(cli): ship search + resource commands` on Wednesday, published as `@elmundi/ship-cli`. The CLI stopped being a shell over some bash scripts and became something you could type into and get useful answers back. Until that week, `ship` was a verb we said to each other; after that week, it was a binary on our PATH.

Third, Apr 18. A Sunday. One commit: `feat(shipctl): v0.9 — artifacts protocol, stack adapters, pharma pilot e2e`. Eleven days into the project we had a protocol — a stable contract between the repo, the CLI, and the backend — and we had it validated end-to-end against a real pilot. One commit. Nobody would guess from the graph what that weekend was.

> Fewer commits is not less work. It is the shape of thinking getting paid back later.

The quiet week is where the spine set. Everything after it is load-bearing on those eight days.

## Apr 19, 13:24: the console is born

Then comes Sunday.

Thirty-five commits on Apr 19. We say "Sunday" because the day matters — nobody on the team was being asked to work, and that is how we knew the project had turned into something we wanted to keep pushing.

The commit stamped 13:24 +0300 is the one we will remember. `RFC-0006: cloud platform foundations + repo-driven onboarding wizard`. That is the moment the Ship Console stopped being a landing page and started being a platform. Twelve days from `Initial import` to a running cloud surface. We are not particularly proud of that number — we just want to write it down so we stop underestimating what twelve focused days can do.

The console was not the only thing that landed that day.

The book was written the same day. Prologue, manifesto, nine lettered sub-chapters, eight field notes. If you read the book now — it's linked from the landing page — you are reading something that was keyed in between commits to the cloud console on a single Sunday. We do not recommend that workflow to anyone. We also do not regret it. Writing the book while wiring up the product kept both honest; neither one had the space to drift into a brochure.

RFC-0005 landed the same day: the artifact folder spec, version 2. On the back of that spec we migrated 61 artifacts into the new layout in two waves, and dropped the legacy format. One codebase, two schemas, one transition, all in one day. A commit named `refactor(infra): remove worker + redis from cloud topology` showed up that evening, which is the kind of refactor you only commit when you have already decided what the new thing is.

And then, because the day apparently had not been enough, the data plane went in: catalog detail, knowledge buckets, a git-sync worker. Workspace ops: members, tokens, audit log. Notion integration and Auth0 OIDC as the first-class identity story. Sentry observability. Caddy and HTTPS. CI images pushed to Docker Hub. The GitHub App. "Day 2" of the onboarding wizard (repo picker, Linear and Notion OAuth). "Day 3" (pipelines, a live dashboard).

Thirty-five commits. One RFC that defined the platform, another that defined the data shape, a book, and the first visible product surface that a customer could actually click on.

We are aware this reads like bragging. It is not. It is a warning. A Sunday like that is only possible because the previous eleven days were quiet enough to build up the charge, and because we had already decided — on Apr 12 — what the product was. Days like Apr 19 are not achievements to replicate. They are receipts for decisions you already made.

## The 53s

Monday and Tuesday were both 53-commit days.

Apr 20 was pilot hardening. Postgres URL normalisation. Bunny Magic Containers auto-deploy on main. The onboarding wizard re-cut to three WOW steps — because five had felt thorough in the diagram and bureaucratic in the browser. A preset picker. Install pipelines that actually ran. A GitHub App that auto-discovered repos instead of making you paste URLs. A multi-preset bundle. An A/B/C/D post-pilot backlog that became D11 (SHIP-book metrics dashboard), D12 (audit-log filters), D13 (pipeline run detail), and — the one we care about most — C12, a real agent: a single-window SSE chat, named buckets, RAG tools. We redesigned the console chat the same day, with typewriter streaming, because watching the agent type is the difference between trusting it and not.

Apr 21 was knowledge. Eight phases of the knowledge-bucket build landed in one day: scope ladder, `bucket_articles` dual-write, retrieval, agent tools, a scope pill in the UI, a Distiller that started as a stub, became an LLM classifier, then grew inbound adapters. A Notion connector fetcher. A Linear issue connector. A per-user memory bucket, because an agent that can't remember anything about you is a toy and we have had enough toys.

Then — still Apr 21 — RFC-0007 landed: lanes-as-config, config schema v2, `shipctl migrate`. Then Wizard v2, iterations one through seven, in that order, in one day: per-repo integrations, a long-lived `SHIP_RUN_TOKEN`, a per-repo agent secrets catalog, per-repo tracker binding, a unified seed-PR endpoint, an onboarding rewrite. Console navigator UX polish: word fade, cards, the `ship-choice` and `ship-todo` widgets, flattened thinking-plus-tool activity, the removal of the Thinking placeholder that nobody had loved. A Playwright e2e tour to make sure the whole thing still booked.

{{chart: blog-stack-mix | Commits by area of the repo. Infrastructure dominates the bootstrap; on Apr 20–21 backend, console, and CLI move together — that is what 'one operating model' looks like. }}

Two 53-commit days in a row costs something. We were tired. We argued about naming things twice. We broke a preview deploy at one point. We merged a config migration that had to be rolled forward in place because rolling it back would have cost more. These are not heroic bugs; they are the ordinary bugs of a team moving faster than its review queue.

What it buys is the thing we set out to buy: one operating model, moving together. The backend does not drift from the CLI does not drift from the console. A change to the artifact protocol rolls through all three in the same afternoon because all three live in one repo and share one release. The chart above is not a story of productivity; it is a story of coupling. In the good sense. The parts move together because they have to.

> Operations live in the mean. You optimise the mean or you optimise fiction.

The second lesson is the one we keep relearning: throughput must be bounded. Two 53s in a row was within bounds because we had decided, the previous Sunday, what bounded it. The moment the bound goes, the commit count becomes noise dressed as progress.

## Apr 22: the quiet day after

Today is the quiet day after. Fifteen commits, and we are calling it done at 11 UTC.

The commits are what you do on a quiet day after a loud one. The console now redirects the greenfield `/` straight to the wizard, because the old "Install everything" CTA was the vestigial organ of the pre-wizard era and it was measurably confusing the first-time viewer. The tracker step banner got softened — two sentences rewritten, nothing more. Versioning got serious: auto-tag on merge, a bundle-drift CI check, a pre-commit hook that refuses to let the CLI and backend disagree about what version they think they are.

And then, somewhat improbably, the Lanes UI got redesigned. Weekly calendar. Outlook-style schedule wizard. A Lanes hub. A three-phase overhaul of Requests. A full catalog cleanup.

On paper this looks like more big work on a "quiet" day. In practice, none of it was structural. The Lanes redesign was six surfaces rewired to speak to the same underlying config we defined in RFC-0007 yesterday. A rewording, a redirect, a pre-commit hook, and a visual refresh of something that already worked — this is what consolidation looks like on a day when you could have instead started the next big push.

We chose not to start the next push. That is the point of Apr 22.

## What the log is telling us

A few things, stated plainly.

- Extraction was cheaper than embedding. Day one looked expensive and wasn't.
- The quiet week set every decision that the loud weekend executed. If we had tried to skip Apr 8–18, we would have spent Apr 19 in meetings.
- The Ship Console existed twelve days after `Initial import`. We do not think that is because we are fast. We think it is because the methodology — the thing Ship is literally selling — worked on us while we were building it.
- Two 53-commit days are a tool, not a target. We will not take three in a row, ever, if we can help it.
- The pre-commit hook we added today will prevent more pain than the chat redesign will create delight. That is usually how it goes.

This is as far as we will go in drawing conclusions. The log is seventeen days long tomorrow; it earns another section then.

If you are reading this and thinking about running coding agents as real delivery workers inside a repo of your own — that is what Ship is for, and the CLI and console are live. Clone, `shipctl adopt`, and the first seed PR lands in your repo in under a minute.

We will write the next one in two weeks.
