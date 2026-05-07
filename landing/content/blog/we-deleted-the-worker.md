---
title: We deleted the worker. The system got simpler.
slug: we-deleted-the-worker
date: 2026-04-21
kicker: Architecture
description: Five moving parts in the morning, two by the end of the day. A worker, a Redis queue, a repo cache, and a git-sync loop — and why deleting them made the Ship Console cheaper, faster, and easier to reason about.
reading_time: 7
author: Denys Kuzin
tags: [architecture, deletions, infra]
---

On April 19 we stood up the Ship Console in the morning. By the evening we were deleting it.

Not the console itself. The scaffolding we'd built around it: a worker, a Redis queue, a repo cache, a git-sync loop. Five moving parts at lunch, two by dinner. The cloud plane now is one API and one console. That is it.

This is the autopsy.

## The shape of it

The morning topology had five things a deploy pipeline had to think about: an API backend (FastAPI on Postgres), the Next.js Console, a background worker, Redis behind the worker, and a second worker that synced customer repos into a persistent cache volume. Each one had a container, a health probe, a set of secrets, and a line in the runbook.

By night, three of those were gone. The API backend runs the synchronous path it was always good at. The Console talks to the API. There is no worker, no queue, no cache. Postgres stays — managed, on Neon. Landing stays, separate and mostly static.

A pilot pushed us to this. Nothing melted. We just spent a morning reading our own architecture diagram and couldn't explain why half of it was there.

## What we deleted

**The worker.** A separate container whose job was to run jobs the API enqueued. It lifted any request the handler didn't want to block on: secret probes, integration health checks, catalog refreshes.

**Redis.** The queue the worker consumed. It was the cheapest queue we knew how to operate, which is to say it still had a URL, a TLS cert, a failover policy, and a bill.

**The repo cache.** A persistent volume that held clones of customer repositories so the worker could read files without a round trip to GitHub. It needed a size limit, an eviction rule, a cleanup job, and its own credentials.

**The git-sync worker.** A second worker whose entire life was pulling the latest commits on a schedule and indexing them into the cache. A loop that existed only to feed another loop.

{{chart: blog-topology-delta | Cloud topology, before and after. We kept the two components that run user-visible work. The rest were load-bearing only against assumptions that did not survive contact with the pilot. }}

We shipped all of that because we believed three things that turned out to be wrong.

We believed secret probes were slow. A secret probe is a short HTTP request to a vendor API that says "is this token alive, does it have the right scopes, can it read the repo we're asking about". It felt like the kind of thing you queue. When we measured it on the pilot accounts, the median was under 200 milliseconds. The 95th was under 500. A synchronous POST handler is a fine home for a 200ms call.

We believed repo content reads needed to be cached out of band. In practice, an agent turn in Ship reads a handful of files — the ones the plan names. GitHub App tokens are fresh and cheap. A direct read, with a small in-process cache bounded per request, was faster than the queued path because it skipped the queue hop. Our cache was saving us a round trip and costing us a container.

We believed we'd need a work queue for long-running agent runs. This is the one that deserves an honest admission. The agent runs don't happen on our box. They happen in GitHub Actions on the customer's repo, spawned by the customer's workflow, billed to the customer's minutes. We don't run the work. We don't need a queue for work we don't run.

The common thread is that none of those beliefs came from measuring the system we had. They came from the shape of systems we'd built before. We reached for the worker pattern before we had a single user.

> Most systems over-scale before they prove correctness. We shipped the worker because we thought we'd need it. We deleted it because we didn't.

## The delete, in commits

Four commits, in the order you'd expect:

- `refactor(infra): remove worker + redis from cloud topology`
- `refactor(integrations): probe secrets synchronously on save`
- `refactor(repo): drop git_sync worker and repo-cache`
- `feat(integrations): GitHub App + Gateway interfaces (pilot day 1)`

The first commit pulled the worker and Redis out of the deploy topology. That was a config change more than a code change: remove the container definitions, remove the environment wiring, remove the health probe. The API still compiled because the queue client had always been behind an interface.

The second commit moved the probe calls into the request handler. The handler already returned 200 on save; now it waits the 200 milliseconds and returns a precise status. If the token is invalid, the user sees it on the save, not 30 seconds later in a status badge that updates from a log tail.

The third commit removed the git-sync worker and the repo cache. Once the read path went through the GitHub App directly, nothing else read from the cache, and nothing needed the sync loop to keep it warm. We deleted the volume. We deleted the cron-like loop. We deleted the spill bucket.

The fourth commit was additive. A GitHub App and a gateway interface — one shape for file reads, one for status checks, one for the webhook. Sized for the operations we actually call, not the ones we might.

Two days later we pushed one more, and it's the one that made the synchronous story feel finished: `Surface precise remediation on agent-secrets probe failure`. If a probe fails, the error tells you what to do. That was the last piece.

## What two boxes earn, and what they don't

The bookkeeping on deletion is larger than it looks.

The Bunny Magic Containers platform we deploy on now has two PATCH targets instead of four. Fewer deploys. Fewer rollback scripts. Fewer things that can be in a partially deployed state at 2am.

The secrets list got shorter. No Redis URL. No worker-to-API auth token. No cache-volume credential. Each deleted secret is one fewer thing to rotate, one fewer grep in the onboarding doc, one fewer note in the incident template about "which environments have this".

Local dev got lighter. `docker compose up` brings up Postgres and the API. No Redis container. No volume mount for the cache. New engineers clone the repo, copy `.env.example`, run two commands. Nobody has to explain what the git-sync worker is or why it has its own log stream.

The mental model got smaller. When something is wrong with a save, the failure is on the save request. There is no asynchronous path that could have swallowed the error 20 seconds ago. One place to look.

> The cheapest worker to scale is the one that isn't there.

Synchronous probes also expose real failures on the first try. The async version had a retry that would mask a bad token for half a minute until the next attempt surfaced it. The sync version returns the 4xx immediately, with a remediation string attached.

Some of what a worker gives you is real, and we want to be clear about what we gave up.

Eventual retries against flaky vendor APIs. If GitHub is having a bad minute, a queue can absorb it. Our answer is less magical: return a precise error, let the caller retry. The human at the keyboard is a decent retry loop for a 500ms call. A CI job is a decent retry loop for a two-second one.

Backpressure for bursty traffic. A queue smooths load. Our traffic pattern isn't bursty. Pilot runs are paced by humans and CI cycles. If that changes, we'll put the queue back — behind the same interface we built the first one behind.

A log of past async work. The worker had a jobs table and we used it to debug. We replaced it with structured logs on the synchronous path, which we needed anyway.

None of those losses feel like the kind that justify a permanent container. If we discover a concrete case that does, we'll add the one piece that case needs. Not the six pieces we imagined around it.

## The principle

Delete infrastructure until something breaks. Then add the piece that broke.

Seven components, five components, three components — none of these are neutral counts. Each one is a deploy target, a secret, a log stream, a failure mode, a restart behaviour, a page in the runbook, a paragraph in the onboarding doc. The cost isn't in the bill. The cost is in the attention.

Operations live in the mean. The mean case for a Ship install is a synchronous save, a direct file read, a commit pushed, a workflow run on the customer's repo. That whole path now goes through two containers.

We spent a morning building a cloud topology that looked like a grown-up system. We spent the afternoon asking which parts of it we could prove we needed. Most of them, we couldn't.

The console the pilot users see is the same console. The latency got better. The bill got smaller. The diagram fits in the palm of your hand.
