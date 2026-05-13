---
title: Bunny Magic Containers, three weeks of fights
slug: bunny-three-weeks-of-fights
date: 2026-04-08
kicker: Field note
description: Sixteen consecutive `fix(bunny)` commits. The Magic Containers API was new, the docs were thin, and our deploy pipeline learned each lesson the same way every team learns it. Here's the shape of the fight, told off the actual commit log.
reading_time: 4
author: Denys Kuzin
tags: [infra, bunny, build-in-public, field-note]
---

If you grep the early git log for `fix(bunny):` you get sixteen commits in two weeks. None of them are interesting on their own. Together they tell a familiar story about deploying to a brand-new platform: the docs are thin, the API surface keeps almost matching what you'd expect from another vendor, and every "almost" costs a day.

This is a short note, not a guide. Bunny Magic Containers is a fine product; it was just very new in April, and the rough edges came in a particular order.

## The shape of the fight

The same loop, sixteen times:

1. Read the Bunny docs.
2. Mirror what we'd done with the previous host (App Platform, Fly, ECS — pick yours).
3. POST to MC fails with a 500 or a vague 4xx.
4. Read the *response body* — not the status code — and discover the API rejects a field the docs implied was optional.
5. Strip the field, retry, ship.
6. Find the next field in step 3 next deploy.

Concretely: the create payload at one point had `name`, `region`, three probe configs, and a sticky-cleanup flag. By Week 2 the payload had `name` and `regions`. The docs hadn't lied; the API had just gotten stricter about *what they actually meant.*

## Three lessons that compounded

**Read the response body.** A 500 with no body is a wall. A 500 with `"field 'foo' not allowed"` is a clue. We added a logging step early because the GitHub Action originally swallowed the body on non-2xx; that one change cut the loop time in half.

**Image digest, not tag.** The first deploy worked. The second deploy fetched a stale image because the Magic Containers fetched by tag and the registry had cached. We switched to PATCHing the digest after a Docker Hub round-trip, with `imagePullPolicy: always`. Two commits to land, hours of debugging to discover.

**Honor the env var first, lookup second.** Bunny's MC creates apps by name; if the name lookup fails, you can pass `BUNNY_APP_ID` directly. We had to learn this twice, because the lookup-then-create flow swallows the case where the name is taken by an app you don't own.

## Why the post

The lesson is not "Bunny is hard." The lesson is: **when you adopt a new infra vendor, your CI is the most expensive learning environment you have.** Each commit in our `fix(bunny):` chain was a real outage of internal velocity — half a day where the team couldn't see their changes deployed.

If you're picking a new infra layer in 2026 — DOKS, Fly, Railway, Bunny, Render, App Platform — budget for *three weeks of CI fights* and treat that as the cost of entry. Not because any of those products are bad. Because every API surface has its lessons, and the docs only cover the ones somebody else has already taken.

## Postscript

The Magic Containers product matured significantly while we were fighting with it. Half the issues above are no longer issues. We use Bunny for landing now (it's good at static + edge), and migrated the live backend off it because of cold-start behaviour, not because of the deploy pipeline.

The CI script — `scripts/bunny-deploy-platform.mjs` — is in the repo. If you're an early-MC adopter, it'll save you a day.
