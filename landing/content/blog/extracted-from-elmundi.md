---
title: What "extracted from elmundi" actually carried
slug: extracted-from-elmundi
date: 2026-04-07
kicker: Origin
description: One commit, six months of methodology, and the LICENSE file that did more work than any line of code. A look at what was actually inside the first import.
reading_time: 4
author: Denys Kuzin
tags: [origin, build-in-public, autopsy]
---

The first commit on the new repo says *Initial import: Ship framework (extracted from elmundi)*. One commit. One day. Twenty thousand lines touching every directory the new project would have for its first month.

It is not what I would have guessed was inside, if you'd asked me on April 8.

## What got carried over

The methodology files — the markdown that describes what an agent does at each lifecycle stage — moved verbatim. The prompts that shipped with each role moved verbatim. The Node runtime that orchestrated the lanes moved verbatim. The MkDocs site that served as both operator manual and agent reading list — also verbatim.

This was the boring stuff. We thought it would be the hard stuff.

## What got rewritten

Three things got rewritten on the first day, and they were the things that decided whether the extraction was real:

**Paths.** Every reference in the methodology that pointed to `tools/linear-agent/` got rewritten to `cli/` paths under the new layout. Every example that used `elmundi/some-pattern` became a generic. The commit body specifically calls out *ElMundi-specific examples gone*.

**The LICENSE.** There hadn't been one before. There was now. Apache-2.0. This is the change that actually made Ship a separate thing from elmundi. The methodology had been "elmundi's habit" for six months; the LICENSE made it "a project that anyone can use." Without it, the rest of the diff would have been moving files into a folder no one was allowed to read.

**The `mkdocs.yml`.** A single line was different: the GitHub link. Pointing at the new repo. Same content, same render, different ownership. That line is the one I keep going back to. It's tiny. It carries everything.

## What was missing

The first commit did *not* include:

- A way to actually run anything outside the elmundi codebase. The runtime worked because it had been tuned against one specific repo. The wider claim — that the methodology travels — was unproven.
- A CLI in any meaningful sense. There were Node scripts. There was no `shipctl`.
- A landing site. There was an MkDocs render of the manual. That was it.
- A backend. The cloud-product angle would not appear until twelve days later.

## The lesson

A methodology you can run inside the repo it grew in is **a habit**. The first commit did not turn it into a product; it turned it into a project. Those are different things, and most of April was spent on the difference.

What the commit actually did was set the boundary: from now on, anything that worked because of *elmundi-specific assumptions* would have to be made explicit and parameterized, or removed. That single move — *we cannot rely on the host anymore* — is what made every later commit possible.

A LICENSE file is a tiny artifact. On April 7, it was the most consequential one in the diff.
