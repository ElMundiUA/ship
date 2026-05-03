---
title: Champagne over teal
slug: champagne-over-teal
date: 2026-05-11
kicker: Brand
description: We swapped the accent colour from teal to muted champagne gold this week. Small change in the diff. Bigger change in how the product reads. Notes on why colour is a positioning lever, not a vibe choice.
reading_time: 3
author: Denys Kuzin
tags: [brand, design, build-in-public]
---

We changed our accent colour last week. Aqua-teal — the bright cyan that had been the brand since week one — got replaced with a muted champagne gold. Hex change. A few hundred lines of CSS swept across landing, console, and docs. Took a Saturday afternoon.

It read different on Monday. *Customers* read it different on Monday.

This is a short note about why we did it, because every founder I've spoken to recently has asked some version of *"how seriously do you take the brand colour?"* — and my honest answer is **more seriously than I expected to**.

## What teal was saying

Teal is a fine colour. It's also a *signal*. Teal in B2B software reads, in 2026:

- Tech demo
- Early-stage SaaS
- ProductHunt indie
- "we just learned Tailwind"

None of which is *wrong* — those are real categories and a lot of great products live in them. But none of them are the category we want a CTO to put us in when they're deciding whether to let our agent run inside their repo. The teal said *we're new and we're playing*. The product is new, sure. We're not playing.

Champagne reads differently. Older. Unhurried. The kind of colour you find on instruments and watches more than on landing-page hero CTAs. It says *grown-up*, and that signal — for what we're selling — matters more than the brightness signal teal was sending.

## The change wasn't subtle

A colour change in a design system isn't one find-and-replace. The accent threads through:

- Primary CTA backgrounds and hover states
- Inline link colour in long-form copy
- Card border highlights on the docs grid
- Status pill backgrounds across the console
- Logo treatments on opengraph images
- Every single button that wanted to read "this is the action"

We swept all of them in one sitting. Half-migrations make a brand look *broken*, not transitional — there's no honest version of "we changed our colour but only on the homepage". Either the product reads consistent or it reads accidental.

The new accent also forced us to reconsider every place where teal was *carrying meaning*. A teal pill in the console used to mean "ok / synced". A champagne pill reads more like "premium / featured". So we kept teal-ish (now a quieter aqua) for system-status signal, and reserved champagne strictly for *operator-facing actions* — the buttons the user is supposed to press. The semantic cleanup was almost half the work.

## What the colour bought us

Three things, in order of how much they surprised me:

**Inbound demos felt different.** Same deck, same product walkthrough, same script. The reaction shifted toward "how do I get my team on this" and away from "interesting, what's the timeline?". A small sample. But the pattern was unmistakable.

**Internal taste calibration improved.** Designers shipping new pages reached for cleaner solutions, because the surrounding palette gave them less permission to be loud. The colour did some of the editing for us.

**Marketing copy felt forced when it wasn't grown-up.** Teal could carry "🚀 ship it" energy and it still felt on-brand. Champagne can't. So the copy got tightened, hyperbole got cut, and the writing read more like *we know what we're doing* and less like *we're hoping you'll tweet about us*.

## The boring lesson

Brand colour is *a positioning lever*. Not a vibe choice. Not a designer-personality thing. The colour changes *which category your visitor mentally puts you in* before they read a word, and it changes *how loose your team feels they can be* with everything around it. Both of those compound.

If the colour you picked when there were three of you working from a kitchen still says *kitchen*, change it. The cost of changing it is one Saturday. The cost of *not* changing it is a year of customers slotting you into the wrong shelf.

We're at champagne now. The lights are on. The room reads grown up.
