---
title: Artifacts are frontmatter now — the RFC-0005 autopsy
slug: artifacts-are-frontmatter-now
date: 2026-04-19
kicker: Autopsy
description: Two files per artifact, one manifest, and two sources of truth that never agreed on a Monday. How we collapsed 61 artifacts into a single-file shape in one day — and why the cleanup commit mattered more than the migration.
reading_time: 9
author: Denys Kuzin
tags: [autopsy, protocol, rfc]
---

On Apr 19 we deleted a concept from Ship: the manifest.

We moved 61 artifacts to a new on-disk shape in two waves, dropped the central index file that had been our catalog's source of truth, removed the loader that read it, and removed the migration code that would have let us read the old shape. All in one day. The number of places where a single artifact is described went from two to one.

This post is the autopsy. What v1 was, why its two-file shape broke us, what RFC-0005 specifies, the four commits that landed it, and the fences we had to add.

## The shape we had

An artifact in Ship is a pattern, a collection, a tool, a workflow, or a lane. In v1, each one lived in two places.

There was a manifest — one big file listing every artifact with its `id`, `slug`, `title`, `kind`, `status`, and `tags`. And there was a markdown body somewhere else on disk, referenced from the manifest by path.

On paper that shape is fine. Metadata over here, prose over there, one index to rule them all. It is how a hundred static-site generators work. It is how package registries work. It reads like a grown-up architectural choice.

In practice, it cost us something every week.

Renaming an artifact was two commits — one to move the body, one to update the manifest row. Creating a new artifact meant editing the manifest, writing the body, and registering the link between them. External contributors would start, miss the manifest step, open a PR with a dangling file, and walk away. We watched it happen more than once.

The deeper problem was drift. The manifest claimed one title; the body's H1 claimed another. The manifest said `status: stable`; the body opened with "this is a draft." There was no force pulling them back into agreement, because nothing in the pipeline read both and compared them.

Then came the Monday. Someone — us — changed an artifact's title in the body without touching the manifest. The catalog in CI kept the manifest title; the landing page rendered the body. The preview deploy on Tuesday failed a cross-link check because two systems disagreed about what this thing was called. Half a day and a preview URL, gone.

> Two sources of truth is one source of truth you can't trust.

That is the sentence we kept coming back to. The manifest was CI's gospel. The body was the human's gospel. We had built a system that required them to agree and gave them no reason to.

## RFC-0005 in one paragraph

One markdown file per artifact. YAML frontmatter at the top — `id`, `slug`, `title`, `kind`, `status`, `tags`, links to related artifacts — is the authoritative metadata. Body is the artifact's prose. The filesystem is the catalog. To list every artifact, glob the tree and parse frontmatter. There is no manifest. There is no loader. There is no registration step. A new artifact is a new file.

That's the whole spec. The interesting part was removing the thing it replaced.

## The four commits

The migration landed in four commits on Apr 19.

The first one was the design: `docs(rfc): RFC-0005 — artifact folder spec v2 (frontmatter as single source of truth)`. It did not move any data. It set the shape. "Single source of truth" is doing a lot of work in that subject line, and we meant it — the whole point was that after this commit, the manifest was a second source of truth living on borrowed time.

The second was `RFC-0005 Wave 1: migrate 61 artifacts to v2 folder layout`. We moved the bodies into the new folder shape and wrote frontmatter blocks for every artifact that had clean metadata in the old manifest. It looks like a monster commit and it is. What it does not do is delete the manifest — both shapes were still on disk after Wave 1, and the loader still read the old one. That was deliberate: we wanted CI green between waves.

The third was `RFC-0005 Wave 2 + cleanup: filesystem-driven artifacts, drop legacy manifests`. Wave 2 handled the 27 artifacts Wave 1 couldn't — aliases, deprecated items, and a handful of cross-links that had become stale in the old manifest and needed hand-resolving. Same commit deleted the manifest files and the loader code that parsed them. After this commit, the filesystem was the catalog. Nothing else read the old shape because the old shape no longer existed.

The fourth is the one that matters most, and it is the shortest: `chore: drop migration code paths (no v1 deployments to migrate from)`.

We had already written a small amount of code to bridge the old shape into the new one — the kind of dual-read scaffolding every sane migration keeps around "for safety." On Apr 19 we deleted it.

The parenthetical in that commit subject is the whole argument. Ship is a young open-source project. There are no v1 deployments in production that we need to gracefully upgrade. There is no customer running an older version of the catalog. The migration code existed to protect a past that didn't exist. So it got deleted the same day we wrote it.

Most companies carry migration code for years. They carry it because one deployment, somewhere, still runs v1, and removing the bridge breaks that one tenant. That is a real cost and a real reason. But if you do not have that tenant — if your users are hypothetical, or if all your users are you — the bridge is pure drag. It is code that describes a problem you do not have, and keeping it means the dual-read logic has to be reasoned about every time someone touches the loader.

We do not get this opportunity again. In six months there will be deployments, users, bridges. The cleanup commit is a receipt from a time before that was true.

{{chart: blog-artifacts-migration | 61 artifacts moved in two waves. Legacy manifests deleted in the same day. No bridge code, because there was nothing to bridge to. }}

## What the new shape bought us

Four consumers. One format.

CI reads the tree to build the catalog index and to validate cross-links. The backend reads the same tree to serve the API that the console and the CLI query. The landing page reads the same tree to render `/patterns`, `/collections`, and every artifact detail view. The book — our long-form documentation — references artifacts by slug and resolves against the same tree. Before RFC-0005, three of those four consumers read the manifest, and the book resolved by scraping H1s. After RFC-0005, everyone reads the folder.

Here is the before and after on the operations that hurt the most.

| Operation | v1 (manifest + body) | v2 (frontmatter) |
| --- | --- | --- |
| Rename an artifact | 2 files changed, 2 commits | 1 file renamed, 1 commit |
| Add an artifact | Write body, register in manifest | Write one markdown file |
| Change a title | Edit 2 places or risk drift | Edit 1 place |

Rename is now one commit and one line in the diff — the file's new path. We did not fully appreciate how often we rename artifacts until renaming stopped hurting.

Discoverability went up almost accidentally. You can `grep` the tree for any tag, any status, any link. You don't need a tool. We had written a tool. It is gone now, and nobody is asking for it back.

The consumer that surprised us most was the agent itself. Under v1, an agent that wanted to add a pattern had to call a custom `update_manifest` tool, and that tool had to know the schema, and the schema had to be kept in sync with CI. Under v2, an agent writes a markdown file. That's the whole interaction. The custom tool is deleted. The prompt for this task is shorter. The failure modes are fewer.

Git diffs also got readable. The old manifest produced diffs like `- title: "Foo" / + title: "Foo v2"` buried in a hundred other rows. The new shape puts the change in the artifact's own file, where a reviewer sees the title change and the body change that motivated it side by side.

> The manifest was versioned by git. But git did not know which row was which artifact, and neither did we.

## The fences we added

Dropping the manifest meant dropping its schema. That was the scariest part of Wave 2. The manifest was, among other things, a contract — it declared which fields every artifact had and which values were legal. Lose the manifest, and any typo in frontmatter becomes a silent bug that only surfaces when something downstream tries to read the missing field.

So Wave 2 shipped a strict frontmatter schema validator, one per artifact kind. Patterns have their required fields. Tools have theirs. Collections have theirs. CI refuses a commit where an artifact is missing `kind`, or declares a `status` we don't recognize, or links to a slug that doesn't exist. A typo now fails a pipeline, not a runtime consumer. This is the cheap version of static typing, and it paid for itself inside a week.

Then the lint rule. If you open a PR that adds a file to the old manifest directory, CI rejects the PR with a message that points at RFC-0005. This is defense against muscle memory — ours, mostly. We built the old shape for long enough that our fingers still wanted to reach for it.

The last fence is a rule: slugs change, ids don't. Frontmatter `id` is a stable, opaque identifier. If you rename a file, the slug may change, but the `id` stays. Every cross-link between artifacts resolves by `id`, not by slug. We did not start with this rule. We learned it in Wave 1, when we renamed one artifact and watched every other artifact that linked to it go stale in the same commit. Restoring those links took longer than the rename saved us. We added the stable id rule that afternoon, and all cross-links got rewritten to use it before Wave 2 landed.

The validator, the lint rule, and the stable id are the fences around the new shape. Without them it would have collapsed back into drift in a month.

## The principle

Three things fall out of this week, and they are worth saying plainly.

Two sources of truth is one source of truth you can't trust. If two files describe the same artifact and nothing forces them to agree, they will disagree, and the cost of the disagreement will land on a Tuesday and on someone who did not cause it. Pick one place. Put the metadata next to the prose, or put the prose next to the metadata — it does not matter which — but do not put them in different files and hope.

Migrations you finish in a day because you have no prior deployments to preserve are the only cheap migrations you will ever do. If you are betting on an open-source project or an internal tool, the best time to make a shape change is before you have users. The cleanup commit on Apr 19 is the kind of commit you cannot write a year from now without breaking someone. We wrote it while we still could.

Frontmatter is a shape, not a format. The format is YAML; we could swap it for TOML or JSON tomorrow and the system would barely notice. But the shape — body-with-metadata-on-top, one file per thing, filesystem as index — is the decision that mattered. Until you pick a shape, you keep paying the two-place-drift tax, regardless of how elegant each place looks on its own.

## Close

On Apr 19 the repo got simpler by one concept, one file type, one loader, and a pile of migration code that was never going to run. The Monday-vs-Tuesday story stopped being possible, because there is no longer a second place for the title to live. The agents got a shorter prompt. The reviewers got readable diffs.

We are not going to claim the new shape is permanent. Shapes rarely are. But it is one shape, in one place, with a validator around it and a rule against growing a second one. That is what we were supposed to have the first time, and it is what we have now.
