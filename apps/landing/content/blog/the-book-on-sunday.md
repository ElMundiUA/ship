---
title: The book was written on Sunday
slug: the-book-on-sunday
date: 2026-04-26
kicker: Field note
description: Prologue, manifesto, nine lettered sub-chapters, eight field notes. Every new passage anchored to a specific commit SHA from a real reference org. All of it keyed in between commits to the cloud console, on one Sunday, in a single session that started after midnight.
reading_time: 5
author: Denys Kuzin
tags: [build-in-public, book, field-note]
---

The Ship book was written on Sunday.

Not drafted. Not outlined. Written — from the Prologue through the Manifesto, through nine new lettered sub-chapters and eight new field notes, through a Preface for the reader, and through the housekeeping that connects all of the above to the forty numbered chapters that already existed. One sitting. One day. We have the commit to prove it, and the ones that came after it to explain why we did it when we did.

This is a short post. It is mostly a receipt.

## The timeline of the day

{{chart: blog-sunday-hours | Apr 19 commits, by hour, in local time (+03). The book lands in the dark before the cloud console does; the cloud console lands in the afternoon. The two halves of the day are not the same project. }}

The earliest commit on Apr 19 is at 00:27 local. It is the book: `docs(framework): book — Prologue, Manifesto, 9 lettered sub-chapters, 8 field notes`. Next to it, six minutes later, is `feat(landing): downloadable book.pdf + Download PDF CTA on /book`. Then the file migration of RFC-0005 Wave 1 and Wave 2, then a mkdocs-mcp experiment we removed the same hour, then a version bump, then a couple of landing-page refreshes, then a catalog pattern for daily retro that had been sitting in a branch for a week.

By 06:00 we were done with the book, done with the migration, done with the landing refresh. The repo took a seven-hour gap — we are being transparent here, that gap is sleep — and then at 13:24 the same day landed `RFC-0006: cloud platform foundations + repo-driven onboarding wizard`, the commit that made Ship a cloud product instead of a documentation site. Everything after that was the cloud build-out.

Two halves of the day. The morning half was a methodology book. The afternoon half was a cloud platform. We wrote the book first on purpose.

## Why the book went first

This is the part worth explaining, because on paper it is the wrong order. A product-minded founder would have written the cloud first and the book after. A documentation-minded founder would have drafted the book in private and shipped it when it was ready. We did neither.

We wrote the book first because we were about to write a lot of code that would put it on a page somewhere else. The cloud console, the landing site, the authored patterns, the onboarding wizard — all of them would end up quoting the book, linking the book, or pointing new users at the book. Every one of those pointers needed the book to already exist as a finished text, not a draft. If we had written the book after the cloud went up, we would have written a book that matched what we happened to have built. Writing it first forced us to build what the book described.

> Writing it first forced us to build what the book described.

The other reason is less principled. A methodology book is a thing that takes a different kind of attention than code takes. It takes the kind of attention you can only give something when you are not being asked to make small corrections to a running system. Sunday before the cloud build-out was the last moment that was true for a long time. We burned that moment on the book because we knew we would not get another one for weeks.

## What made it into the book

Nine new lettered sub-chapters, threaded into the existing forty numbered chapters without mutating a single existing body. The commit message lists them:

- **Part on artifacts as work objects** — Chapters 18.A through 18.D (artifact as the unit of change, reading a pattern, authoring a new pattern, versions / channels / yank).
- **Part on operator's dashboard and economics** — Chapter 20.A (five honest metrics vs. forty cheerful ones), 28.A (regulated-vertical overlays via the pharma addendum), 31.A (cost arithmetic and the envelope-not-bill alarm).
- **Chapter 22.A** — evals for prompt artifacts.
- **Part on the improvement loop** — 25.A through 25.C (where a fix becomes feedback; telemetry that serves operators; agent regression triage).
- **Chapter 33.A** — how humans review what agents wrote.
- **Chapter 35.A** — onboarding a human into an agent team.

Every new passage is anchored to a specific commit in a reference org. The commit body notes this directly: `Every new passage is anchored to a real commit in the reference org; the SHA-by-SHA map ships alongside as documentation/framework/examples-plan.md`. That anchor file is an internal map from a book passage to the commit in ElMundi's history that the passage is describing. If the history gets rewritten later, or we open-source a different reference org, the anchor file lets an editor swap the SHA without rewriting the prose.

Plus eight new field notes — single indented admonitions inside existing chapters, with the chapter body untouched. The commit lists them: Ch. 4, Ch. 15, Ch. 19, Ch. 23, Ch. 39, Ch. 40, and the pre-existing notes in Ch. 5 and Ch. 24 preserved.

Plus a Prologue (_The night the agent shipped nothing_), a Preface, and the Manifesto before the Vocabulary — three new framing passages that did not exist before.

Plus the housekeeping: update the top-of-file "Jump" line, update the reading-time intro to reflect the new structure.

One commit. One SHA. `c9d69f9`.

## The PDF, six minutes later

The second commit of the day is `feat(landing): downloadable book.pdf + Download PDF CTA on /book`. Six minutes after the book landed, we had a downloadable PDF and a button on the landing page pointing at it.

This seems like a small thing. It is not. A book that only exists as a set of Markdown files on a Next.js route is a blog, even if it is long. A book that exists as a PDF you can hand to someone, with a consistent font and a cover and a page number, is a book. The distinction is what decides whether the thing gets treated like reference material or like content marketing. We wanted the former. The PDF was not an afterthought; it was the other half of calling the text a book.

A few hours later, at 05:01, we redesigned that PDF. The commit is `landing: book PDF redesign + ECharts SVG infographics`. Typography pass, infographics generated from the same ECharts pipeline we now use for this blog, a better cover, a real table of contents. Three passes of the same PDF in one night.

## The part that is not glamorous

Between the book, the RFC-0005 waves, and the landing-site refresh, the overnight session was nine hours of work by two tired people on a Saturday-into-Sunday. The commits show the pattern: a writing commit, a code commit, a docs commit, another writing commit, a PDF redesign, a chore, a version bump. We did not write the book and then write the code. We moved between the two.

That rhythm kept the book honest. Every time the prose got abstract, a code commit forced it back to something concrete. Every time the code drifted, a paragraph in the book forced it back into a shape that could be written about. The book and the product kept each other in sync, because both were being committed to the same repo, under the same pair of hands, in the same hour.

We do not recommend this schedule. Writing a book overnight is a cost we do not want to repay. But the ordering mattered: methodology first, cloud after, both in the same day, both with a commit receipt.

> The book and the product kept each other in sync because both were being committed to the same repo.

## What the book is, now

If you read the book today, it is linked from the landing page at `/book`. You can also [download the PDF](/book.pdf). The SHA-by-SHA anchor file is `documentation/framework/examples-plan.md` and is shipped inside the repo. The nine lettered sub-chapters and eight field notes that landed on Apr 19 are in there verbatim, with light continuity edits since (about a dozen commits — we can show them on request).

Nothing in this blog post argues that you should write a book in a day. It argues that if you are going to write a book at all, you should write it before the thing the book describes exists in its final form, and you should write it in the same repo the thing ships from, so the two keep each other honest.

A Sunday is enough if the day is chosen. This one was.
