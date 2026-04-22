---
title: The protocol before the product
slug: the-protocol-before-the-product
date: 2026-04-18
kicker: Case study
description: Between the Apr 7 extraction and the Apr 19 cloud console, there is a quiet 11-day stretch that looks like nothing happened. One commit on a Sunday did the whole thing — shipctl v0.9. We wrote the protocol before we wrote the product; here is how and why.
reading_time: 10
author: Denys Kuzin
tags: [protocol, cli, autopsy, build-in-public]
---

Between Apr 7 and Apr 19 there is an eleven-day stretch of the Ship graph that looks like silence.

It isn't. It is the part of the project where we wrote the contract before we wrote the software meant to speak it. One commit on a Sunday did most of the work, and by the time the cloud console showed up a week after that, the protocol it spoke was already running through `shipctl verify --no-network` against a real pilot. This is the autopsy of that stretch.

## The silence

Apr 8 through Apr 17 is the part of the log the outside observer would close the tab on. Five commits on Apr 12. One on Apr 15. Four on Apr 16. Nothing on Apr 17. If you treated the graph as the project, you would say the project stalled.

The Sunday that came next was 26 commits, and we wrote about some of those in *Ship — the first two weeks*. This post is about the eleven days before Sunday. Specifically it is about the three moments in those days where the decision that held up the following Sunday was made, and it is about the single commit on Apr 18 that packaged those decisions into a product.

We are writing this down because we want to stop pretending the loud Sunday was the miracle. The loud Sunday was the receipt. The week before it was the work.

## Apr 12: docs are not the product

Ship started life as a folder of methodology inside someone else's application. When we extracted it on Apr 7, we extracted a documentation site — MkDocs, a `mkdocs.yml`, a runtime container, a landing that was really a sidebar. For five days after extraction, we kept it that way, because a documentation site is a shape that exists and "a methodology platform" is a phrase.

On Apr 12 we pushed one commit and stopped pretending.

`feat: landing app, ship CLI, backend API; retire MkDocs runtime`.

Five nouns in one subject line. A landing app — marketing, a manual, a book, a catalog view. A CLI that knew how to read patterns, tools, workflows, and collections. A FastAPI backend that served search, fetch, feedback, and pattern metadata. A monorepo layout where those three things could be built from one checkout. And the retirement: MkDocs gone, the old Node runtime package gone, the docs pipeline replaced by Next's static output.

The interesting move was not adding three surfaces. It was the retirement. We had been treating the methodology as a documentation site that the product would eventually wrap. That commit reversed the direction. The docs were no longer the product. They became an artifact the product consumed — patterns, tools, workflows, collections, all rendered by the landing, served by the backend, fetched by the CLI.

> Docs as the product is a shape. Docs as an artifact the product consumes is a contract. You only get the second one by giving up on the first.

Retire is a strong word for a subject line. We picked it on purpose.

## Apr 15 and Apr 16: one shape for four things

Two days, three commits, one idea.

Tuesday: `Unify methodology API for catalogs and add npm publish workflow`. The FastAPI backend had started life with one verb — `/patterns` — because patterns are the oldest thing in the methodology and the first thing we wrote. Everything else (tools, workflows, collections) had been its own half-built loader somewhere. The unification commit exposed `GET /tools`, `/workflows`, `/collections` and their by-id twins next to patterns, search, fetch, and feedback. The CLI got a single `SHIP_API_BASE` and used it for every remote catalog command. One URL. One shape. Four kinds of thing.

Wednesday: `feat(cli): ship search + resource commands`, and then, the same day, `chore(cli): publish as @elmundi/ship-cli under npm org elmundi`. The search command made the CLI answer questions — type a query, get the artifacts that match, across all four kinds. The resource commands — `pattern`, `tool`, `workflow`, `collection`, each with `list` / `show` / `fetch` / `search` — gave every kind of artifact the same verbs. Then we published the whole thing to npm as `@elmundi/ship-cli` at version 0.8.0.

> `ship` was a verb we said to each other until that week. After it, it was a binary on PATH.

A CLI you can `npm install -g` is not the same thing as a CLI you run out of the repo. The rename-to-a-scoped-org was not a marketing move; it was a commitment. It said: the shape of the API, the shape of the CLI, the shape of the artifacts — those are things we are prepared to have users depend on, because we just told npm they could.

Four kinds of artifact, one API, one CLI, four verbs each. None of that is a product yet. But it is the armature a product can be hung on without rewiring the wall.

## Apr 18: the commit that was the week

Apr 18 was a Sunday. There is one commit on it. The subject line is sixteen words long.

`feat(shipctl): v0.9 — artifacts protocol, stack adapters, pharma pilot e2e`.

The body is three pages. We will not paste it. What it did, in the order it matters:

It specified the protocol in four RFCs, and then it shipped code that obeyed them. **RFC-0001** defined the artifacts protocol v1: every artifact carries a `version`, a `content_sha256`, an `updated_at`, a `channel`, a `min_shipctl`, and the yanked / deprecated / replaced_by fields a registry needs to evolve without lying. **RFC-0002** specified `.ship/config.yml` — the on-disk config that turns a repo into a Ship workspace. **RFC-0003** specified telemetry and feedback — opt-in, outbox-based, anonymous-id scoped, rate-limited, with export and delete endpoints because any protocol that collects anything has to say how to leave. **RFC-0004** specified adapters — the interface that lets one CLI speak to thirteen coding agents, seven trackers, and seven CI systems without the methodology book learning any of their names.

It renamed the binary. `ship` became `shipctl`, with a deprecation alias for the old name. That sounds cosmetic. It isn't. `ship` is a verb the product borrows from the user; `shipctl` is the tool that operates on a Ship workspace. The rename drew a line between the methodology's name and the CLI's name, which was the line we had been eliding for a week.

It added the verbs. `shipctl init`. `shipctl new`. `shipctl sync`. `shipctl verify`. `shipctl doctor`. `shipctl config`. `shipctl search`. `shipctl fetch`. `shipctl collection fetch`, `tool fetch`, `workflow fetch`. `shipctl telemetry` (status / on / off / flush / export / delete). `shipctl feedback` (draft / list / show / submit / remove). Every verb is a line in the protocol, expressed as something a human types.

It wired the backend. `/manifest` lists artifacts. `/<kind>/{id}/versions` exposes history. 410 for yanked, 200 for deprecated. `POST /telemetry` returns 202 with a 60-batch-per-minute ceiling per `anonymous_id`. `POST /feedback` deduplicates by label. Export and delete endpoints exist because they have to. A GitHub Actions workflow — `artifact-check.yml` — guards `content_sha256` against the manifest on every PR, because a protocol that doesn't refuse drift is not a protocol.

It shipped fourteen adapter modules. Thirteen coding agents — cursor, codex, claude, claude-md, agents-md, copilot, aider, cline, continue, windsurf, zed, gemini, opencode — each with a `detect()` that returns confidence and evidence. Seven trackers. Seven CI systems. A `doctor` command that reconciles `.ship/config.yml` against those detectors and never recommends a stack that contradicts what's already there.

And then it proved the whole thing worked. End to end. On a real pilot.

One command — `shipctl new /tmp/pharma-pilot-demo --preset mobile-app --tracker linear --ci gh-actions --agents cursor,claude-md,codex --language ts --yes` — created a greenfield workspace with a pharma-mobile preset. A second command — `shipctl verify --no-network` — exited 0 with 7 pass, 0 fail, 4 skip. A drift test — a single byte appended to a cached artifact — caused `sync` to re-fetch automatically, and `verify` to go green again.

That is a protocol, being a protocol.

One commit. One Sunday. A rename, four RFCs, fifteen CLI verbs, twenty-plus adapter modules, seven backend endpoints, a CI guard, a pilot, a rerun, and a green verify.

{{chart: blog-cli-surface | shipctl verbs by category, in the shape they landed. Lifecycle first, config and cache next, verification and catalog in the middle, telemetry and feedback at the edges. One Sunday drew the whole surface. }}

## What the protocol is

It is worth saying out loud what we mean by "protocol," because the word gets used for anything.

A protocol, in the Ship sense, is the thing that two processes agree on so that neither of them has to know the other's implementation. The `.ship/` directory on a user's disk is a protocol. The `/v1` backend URL space is a protocol. `content_sha256` is a protocol — because every process that reads an artifact can compute it, every process that writes one can stamp it, and no process has to trust any other process's memory of what the artifact said.

In the `shipctl v0.9` commit, the protocol was four things at once.

**The on-disk shape.** `.ship/config.yml` at the root. `.ship/cache/` for fetched bodies. Artifacts with YAML frontmatter, `install_target` in the frontmatter for collection-as-rules, `min_shipctl` declaring the floor. A layout a human can read with `ls` and a script can read with a glob.

**The wire shape.** `/manifest`. `/patterns/{id}/versions`, `/tools/{id}/versions`, `/workflows/{id}/versions`, `/collections/{id}/versions`. `POST /telemetry`. `POST /feedback`. Export and delete. Status codes that mean what they're documented to mean.

**The integrity shape.** Every artifact has a `content_sha256`. `sync` re-fetches on drift, not just on stale metadata. `verify` checks the cache against the manifest. The CI workflow checks the manifest against the tree. The hash is the same hash at every hop, because no hop recomputes it into a different shape.

**The adapter shape.** A `detect()` that returns confidence and evidence. Hooks reserved for `bootstrap` and `verify`. A clear answer, per stack, per target, about whether this adapter applies and why. That is what lets one CLI cover thirteen agents without knowing what any of them are specifically.

None of those shapes is interesting in isolation. Each one is a small thing — a file, a URL, a hash column, an interface. The protocol is the fact that they all agree on what the other ones mean. A change to the artifact's `content_sha256` is enforced by CI, recorded in the manifest, picked up by `sync`, validated by `verify`, and surfaced by the backend. A change nobody signs off on dies at the first hop. A change everyone signs off on flows through all four.

That is what was on disk by the evening of Apr 18. None of it was pointed at a customer yet. All of it would be, the following Sunday.

## Why we wrote it before the product

A protocol looks like overhead when the product isn't built. You can always point at the protocol and say: we could have shipped a working thing by now, if we had not spent the week writing the shape of the thing we are going to ship.

We chose the protocol-first order for three reasons. All three survived contact with the week after.

The first is that a protocol is the cheapest thing to change before it has consumers. On Apr 18, the only consumer of the artifacts protocol was the pharma pilot. One consumer means one migration. On Apr 20, there were three consumers — the CLI, the backend, the landing. By Apr 22 there were five, with the console and the book resolving against the same tree. Every consumer added after Apr 18 inherited the shape; none of them got a vote on it. That is the tax we did not want to pay later, and we paid it early by writing four RFCs nobody had asked for.

The second is that a protocol forces you to say what the product is before you build it. The commit body had to enumerate every verb the CLI would have, every endpoint the backend would serve, every field every artifact would carry. You cannot write that enumeration without deciding what is in scope. The scope decisions we made on Apr 18 — no server-side agent execution, no proprietary artifact format, adapters as the extension mechanism, telemetry behind an opt-in outbox — are the decisions that bounded the next four weeks of work. Without them, Apr 19 would not have been a product day; it would have been a scope debate day.

The third is that a protocol gets validated by a pilot, not by a demo. The pharma pilot in the Apr 18 commit is the part we care about most. `shipctl new ... --preset mobile-app`. `shipctl verify --no-network`. 7 pass, 0 fail, 4 skip. That output is the proof. It is not proof that the product is finished — the product isn't finished — but it is proof that the protocol is internally consistent. A human ran a real command against a real workspace and a real backend, and the contract held.

> Protocols live in the mean. Products live at the edge. You write the protocol so the edge has somewhere to stand.

We had heard variations on the protocol-first advice before and mostly disregarded them. The version that stuck is the one we learned by doing: a protocol is what a team writes on a day nobody is asking them to. If you try to write it when a customer is asking, you write a feature. If you try to write it when a deadline is pressing, you write a workaround. Apr 18 was a Sunday. Nobody was asking. That is the only kind of day a protocol gets written on.

## What Apr 19 inherited

The morning after Apr 18, the cloud console started landing. `RFC-0006: cloud platform foundations + repo-driven onboarding wizard`. A FastAPI `/v1` backend. An onboarding wizard. An auth story. A repo picker. Thirty-five commits on one day.

None of those commits defined the artifacts protocol, because the artifacts protocol was already defined. The console read `/manifest`. The wizard installed the preset workflows that RFC-0004 adapters had specified the day before. The seed-knowledge endpoint wrote artifacts whose `content_sha256` the CI guard would check on the next PR. The telemetry outbox shape was already decided, so the console's first analytics events dropped into the same endpoint that `shipctl telemetry flush` used.

We are not claiming this was magic. We are claiming it was cheap, relative to what it would have cost to define the protocol concurrently with the console. The cloud console's first pilot on Apr 20 — 53 commits, real users, a repo, a real installation — did not uncover a protocol bug. It uncovered product bugs. Onboarding copy. Loading states. Probe error messages. The kinds of things you want a pilot to find, because the kinds of things you do not want a pilot to find are the ones you cannot fix without breaking a contract.

## The principle

Two things worth saying plainly.

Write the protocol first, and write it on a day nobody asked you to. Protocols are contracts between parts of a system that have to agree without meeting. If you write them while a customer is watching, you will optimise for what the customer wants to see this week, which is almost never what the system needs to hold together next month. A Sunday with nothing on the calendar is the best day of the year to write a protocol. You will get one or two of those days per quarter if you are lucky. Spend them on this.

Ship the first version with a real pilot. Not a unit test. Not a mock. A real pilot. The pharma-mobile pilot in the `shipctl v0.9` commit is the thing that told us the protocol worked, and it is the thing that will tell us — the next time we change it — whether the change survives contact. 7 pass, 0 fail, 4 skip is an unimpressive number. It is also the number that let us ship the cloud console the following weekend without holding our breath.

The Ship cloud console is what people see. The protocol is what they do not see. One of those two things is six days older than the other, and the older one is what everything else is load-bearing on.

The next time the graph goes quiet, read it the other way around.
