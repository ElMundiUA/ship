---
title: Wizard v2 — the art of saying no
slug: wizard-v2-the-art-of-saying-no
date: 2026-04-24
kicker: Case study
description: Ten steps became three, then stayed three across seven backend rewrites in one day. A case study in keeping a flow honest while the mechanism under it moves.
reading_time: 8
author: Denys Kuzin
tags: [onboarding, product, case-study]
---

Wizard v1 had ten steps. Wizard v2 has three. The delta is not fewer features — it's a different theory of the user.

We shipped v1 on Apr 19. We cut it to v2 on Apr 20. Then we rewrote the backend underneath v2 seven times on Apr 21 without adding a single user-visible step. This is the story of those three days, told as honestly as we can tell it.

## What v1 was trying to do

Be fair to past-us for a second. Wizard v1 was built on a reasonable instinct: the user is about to hand a coding agent write access to their repo, their tracker, and their CI. We should validate everything before they do anything. We should show them we know what we're doing.

So v1 validated everything. Ten user-visible steps: pick repo, clone it, pick tracker, authorize tracker, pick preset, review preset diff, install preset, verify install, seed the knowledge base, invite the team. Each step had its own loading state, its own error surface, its own back button. Underneath those ten steps sat six side-effects the user had to reason about: the probe, the clone, the invite, the preset review, the verify pass, and the seed.

The telemetry was not kind. Users dropped off at steps 4 through 6 — right in the middle, right where the flow stopped being about them and started being about us. The ones who made it to the end saw a repo tile labeled **ALREADY CONNECTED** that, it turned out, was connected in exactly one of the six senses we cared about. The other five were still undone. The banner was lying. We were the ones who wrote it.

The worst thing about v1 wasn't the dropout. It was the support load. Every user who finished onboarding generated a ticket within 48 hours, because the flow had told them they were ready and the system didn't agree.

## The re-cut

On Apr 20 we shipped one commit:

`wizard: re-cut onboarding to 3 WOW steps + drop repo-cloning legacy`

That subject line is the whole turning point. Two moves in one commit. We cut the flow from ten steps to three. And we deleted the repo-cloning code — the cloud plane used to clone customer repos locally so it could "validate" them, a decision we wrote about separately in *We deleted the worker*. Removing the clones removed four of the six side-effects by itself. The probe still had to exist. The install still had to exist. Everything else was theatre we'd staged for ourselves.

The re-cut was not a redesign. It was a deletion. We did not draw new screens. We deleted eight of the old ones and kept the two the user actually needed, plus one we owed them.

## Three steps that produce artifacts

Here is v2, in full:

1. Connect a repo. GitHub App install, auto-discovery of which repos you want to expose. Produces a repo binding.
2. Pick a tracker and a preset. Linear, Jira, GitHub Issues, or Notion. One preset from the catalog. Produces a selection.
3. Install the preset as a PR. Ship opens a pull request against the repo you picked, containing the methodology files. Produces a seed PR and, on merge, a running pipeline.

Each step produces a visible artifact in the user's repo. That is the whole principle. The user doesn't have to believe us about whether onboarding worked, because the thing is there, in a place they already trust. A binding shows up in Settings. A PR shows up in their review queue. A pipeline shows up in Actions.

> Each step produces a visible artifact in the user's repo. That is the whole principle.

We stopped asking the user to trust our state. We started writing state into theirs.

## Seven rewrites, zero new steps

{{chart: blog-wizard-steps | User-visible steps stayed at three. Side-effects the user must reason about moved around but never exceeded four. The whole point of wizard v2 was to move work under the flow, not through it. }}

On Apr 21 we shipped seven iterations of v2. Not seven features. Seven rewrites of the mechanism underneath the three steps. From the user's seat, the flow did not change. From our seat, almost everything did.

**Iter 1. Per-repo integrations + repo callback-token schema.** Integrations used to be workspace-global. The database said so. Reality did not. A user with three repos has three different sets of agent secrets — different deploy keys, different tracker credentials, different branch protections. We were pretending one workspace had one tracker. It never did.

**Iter 2. Long-lived `SHIP_RUN_TOKEN` + dual-mode callback auth.** Short-lived JWTs were expiring mid-run. The agent would finish a 40-minute task, try to post the result back, and get rejected by its own auth layer. We added a long-lived token for runs and kept JWTs for everything else. No new step. A failure mode, fixed.

**Iter 3. Per-repo agent secrets catalog + check-or-push API.** The probe now knows exactly which secrets each repo needs and can surface which ones are missing, by name, with the remediation. This is the iteration that killed the lying banner. **ALREADY CONNECTED** got replaced by something that actually corresponds to whether an agent could run right now.

**Iter 4. Per-repo tracker binding API.** Iter 1 made integrations per-repo. This made the tracker binding per-repo on the API surface. A workspace doesn't have a tracker; each repo has one, possibly a different one, possibly none. This added one visible side-effect — the user can now see "repo A is bound to Linear, repo B is bound to GitHub Issues" — which is fine. Visible is fine. Hidden is the problem.

**Iter 5. Unified seed-PR endpoint + tracker FSM doc.** One endpoint installs any preset. We also wrote down the tracker state machine so a user can reason about why a card is yellow instead of green. The FSM was in our heads and in the code. It is now also on a page.

**Iter 6 and 7. Onboarding rewrite + tracker FSM settings.** Copy edits on the three steps. A settings surface that exposes the tracker FSM. No new step in the flow. The settings page is where you go after you've already succeeded, not during.

Seven commits. Same three screens. The side-effect count walked from 2 to 3 to 4 over the course of the day, and stopped there. Four side-effects, all surfaced as settings the user can inspect later, none of them blocking the path to a running agent.

The framing that kept us honest: iterations move work **under** the flow, not **through** it. Every time a rewrite threatened to add a step — "we should really ask the user about this" — we asked instead whether the default would be wrong often enough to justify the question. Usually it wasn't. When it was, we added a settings row, not a wizard step.

## Three copy fixes worth shipping

On Apr 22 we shipped a cluster of follow-ups. None of them changed the flow. All of them were about telling the truth.

`Reword GitHub Issues tile pill — 'ALREADY CONNECTED' was misleading.` The pill now says what it means: whether the tile is a candidate, wired, or running. Three states, three words. We had one word doing the work of three and lying about two of them.

`Retire legacy 'Install everything' CTA; funnel to the wizard instead.` This was the escape hatch. The one-click button that promised to do the whole setup without the flow. Users who took it came back as support tickets, every time. We killed the button. The only way through is now the three steps. A flow you don't let people bypass is a flow you have to make worth walking.

`Console: redirect greenfield workspace to wizard on /.` A blank console used to route to a fake dashboard — one with empty charts and a friendly "come back later" card. Now a blank console routes you to the wizard. There is no other sensible place for a user with no repos to be.

Plus two smaller fixes from the same day:

`Make wizard step 4 resilient to single-probe failures.` One flaky vendor probe shouldn't block the flow. If a probe times out, we mark it unknown, let the user continue, and retry in the background. The seed PR still opens. The user still sees a result.

`Surface precise remediation on agent-secrets probe failure.` Errors now say what to do. Not "probe failed." Not "unauthorized." The exact env var, the exact scope, the exact place to fix it.

Copy is architecture. You can tell because a copy change can make a system feel like a different product. **ALREADY CONNECTED** vs **READY TO RUN** is not a word swap. It's a promise you're choosing to keep or break on every page load.

## The principle

You don't design an onboarding flow by listing what's necessary. You design it by listing what's necessary and then saying no to everything that's merely relevant.

> Saying yes to three things means saying no to six.

Ten steps felt thorough. Three steps are honest. The difference is that a three-step flow forces you to do the hard work — the per-repo integrations, the dual-mode auth, the honest probe, the unified endpoint — *underneath* the flow, where the user will never see it but will feel it. A ten-step flow lets you duck that work by turning each gap into a screen.

Operations live in the mean. A wizard is an operation. Its mean case is a user in a new repo on a Tuesday who wants a coding agent to do something useful by lunch. If the flow serves that user, the long tail can be handled in settings, in docs, in support. If the flow serves every edge case, the mean user drops off at step five.

We are going to keep the count at three. If we want to add something, we will ask first whether it belongs in the flow or underneath it. If it belongs underneath, we'll put it underneath, even when that's harder. Especially when that's harder.

Fences exist on purpose. Three is a fence.
