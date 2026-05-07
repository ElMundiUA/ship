---
title: adopt-ship.sh and the wrong adopters
slug: adopt-ship-launcher
date: 2026-04-13
kicker: Field note
description: A 60-line shell script meant to make adoption frictionless. The first three people who ran it did the wrong thing in three different ways. What we learned about the gap between "easy to start" and "easy to start correctly."
reading_time: 4
author: Denys Kuzin
tags: [adoption, onboarding, build-in-public, field-note]
---

`adopt-ship.sh` was a shell script in the repo root. Sixty lines. It cloned the seed bundle into a target repo, copied the agent rule files into `.claude/`, ran `npm install`, and printed a green message.

I'm sharing this because it's a small, perfect example of the gap between *making something easy to start* and *making something easy to start correctly*. The two are different problems and we kept conflating them.

## What the script did right

You ran it from any repo. It did not require you to read the docs. It worked on day-zero macOS and Ubuntu. The success message linked back to the next step. By the standards of "first impression," it was great.

We watched three different people run it in the first week. All three got to the green message. None of them ended up with a working Ship adoption.

## What went wrong

**Adopter #1** ran the script in a fresh repo with no commit history. The seed bundle assumed a git remote was configured; the script didn't check. Their `.claude/` was set up correctly, but their CI couldn't trigger because there was no `origin`. They quietly stopped using it.

**Adopter #2** ran the script in a monorepo with three apps. The script copied agent rule files to the repo root. Two of the three apps couldn't see them because they expected `.claude/` adjacent to their own `package.json`. The agent ran on the wrong codebase for two days before anyone noticed.

**Adopter #3** ran the script and hit a broken `npm install` because their lockfile pinned an incompatible peer dependency. The script reported success because `npm install` had a non-zero exit but the green message printed anyway. Bug on our side, and one we'd never have caught without watching a real adopter.

Three runs. Three different failure modes. None of them were "the script crashed."

## What we changed

We didn't fix the script. We retired it.

The wizard that replaced it (later: Wizard v2) does six things the shell script did not:

- Refuses to run if the target is not a git repo with a remote configured
- Detects monorepo shape and asks where the agent rule files belong
- Validates the install actually worked, not just that npm exited
- Records the user's answers as a manifest that the maintainer can audit
- Surfaces drift between the seed bundle and the target after install
- Gates publish on workspace defaults being set

Each of those costs you a hundred lines instead of zero. Each of those was learned from a real adopter doing a real wrong thing.

## The lesson

A "frictionless onboarding" that lets the user complete the happy path while sitting in a broken state is **worse than friction**. It tells them everything's fine when nothing is. Friction in the right place — *we noticed your repo has no remote; here's how to add one* — is the opposite of bad UX. It's the script doing the work the user shouldn't have to know to do.

The metric *did the user get to "done" without crashing* is the wrong metric. The right metric is *did they end up in a configuration that actually works.*

`adopt-ship.sh` got the first metric right. It got the second one wrong, three times in a row.

We don't ship one-shot install scripts anymore.
