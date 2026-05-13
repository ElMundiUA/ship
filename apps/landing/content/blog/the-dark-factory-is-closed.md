---
title: The dark factory is closed
slug: the-dark-factory-is-closed
date: 2026-05-04
kicker: Manifesto
description: "The dark factory — Foxconn lights out, robots making robots — is the wrong metaphor for what AI agents do to software teams. The team isn't shrinking; it's becoming the part that decides. And that part runs on more brains, not fewer."
reading_time: 8
author: Denys Kuzin
tags: [philosophy, agents, teams, build-in-public]
---

There's a metaphor making the rounds: **the dark factory**. Foxconn assembly lines that ran for a year with no human inside. Lights off, robots welding robots. And every few weeks someone applies it to software teams. *Soon you'll have one engineer running an army of agents. The room will be dark. The team will be five people instead of fifty.*

It's wrong. Not because the productivity numbers are wrong — they aren't. It's wrong because *software was never the kind of thing you can dark-factory*.

Foxconn knows what it's making before it makes it. Every screw, every gasket, every plastic shell is specified in a CAD file someone signed off on six months ago. The factory's job is to execute the spec at scale. Lights go off because the spec *is* the product, and the spec doesn't change while the line is running.

A product team doesn't have a spec. It has a hypothesis, a rough deadline, and a thousand small decisions a week that the spec can't make.

## What you actually ship

You don't ship code. You ship judgments — about which problem is worth solving, which user actually has it, which tradeoff is the lesser one this quarter, which feature can be cut without losing the customer. The code is a *side effect* of the judgment. Ship the wrong judgment and the code doesn't matter — you built the wrong thing perfectly.

This isn't a soft observation. It's why dark-factory math doesn't apply. The spec at Foxconn doesn't argue with you on Tuesday because a customer call changed the picture. Yours does. That's the whole job.

## What the agent actually changed

Everything the agent took off your plate sits in the *executing* half of "decide, execute". Code typing. Boilerplate fixes. Test scaffolding. Doc rewrites. The thousand 30-minute tickets that used to fill a day. Real work. Boring work. Necessary work. *Not the bottleneck.*

The bottleneck moved. It used to be: "we know what to do, we just need to type faster." Now it's: "we have eight ways we could go this week and we have to pick three." That's not a typing problem. That's a *thinking* problem.

And here's the inversion that breaks the dark-factory model: **the agent's speed makes the thinking bottleneck worse, not better**. When code shipped in two-day cycles, you had two days to decide what next. When code ships in two-hour cycles, you have two hours. You can shorten the development loop until it costs nothing — and the team that has to feed it ideas at that rate will burn out unless something changes.

The thing that changes isn't headcount. It's *who's deciding*.

## One brain runs out of ideas

Try this experiment. Hand a single very smart engineer an army of agents. Give them a quarter to ship as much as possible. Watch what happens.

Week one: throughput is great. Three weeks of normal sprint volume.

Week three: the engineer is reusing the same five mental templates. The agents are great at executing what they're told. They're terrible at noticing that the *problem* changed.

Week eight: there's a feature shipped — perfectly, on time, with tests — that nobody wanted. One brain can't hold a customer surface, a competitive surface, a technical surface, a cost surface, a hiring surface, *and* generate fresh ideas from each in parallel. **One opinion running at AI speed is just confidence with no calibration.**

This is the new failure mode. Not bugs. Not slow shipping. *Confident shipping of the wrong thing*, faster than ever, by a single brain that ran out of fresh angles around mid-quarter.

## Collective intelligence is compute for direction

You know how you fix it? More brains. *Different* brains. The exact thing the dark factory was supposed to remove.

A team isn't a redundancy mechanism for typing — agents fixed that. A team is a **direction-generating mechanism**. Five people who think differently will find more product moves in a week than one person can find in a month, because their *blind spots don't overlap*. The PM who came from sales sees pricing levers the engineer doesn't. The engineer who shipped two products before sees a third option the designer wouldn't reach for. The new hire asks the dumb question that turns out not to be dumb. Multiply that by Slack threads, by review comments, by half-arguments at lunch, and you have something a single very smart person literally cannot replicate.

Diversity here isn't an HR phrase. It's *compute for blind spots*. Different backgrounds → different priors → different hypotheses generated per unit of evidence. That's not soft-skill territory. That's parallelism.

The agent ships fast. Only a *team* moves direction fast.

## What the room actually looks like

Lights stay on. The room isn't full of typists; the typists are the agents and they don't need a chair. The room is full of people doing the work the dark-factory model says doesn't exist:

Reading customer transcripts. Arguing about which of three features to cut. Drawing on whiteboards to find the abstraction that explains all the bug reports at once. Calling a vendor. Deciding to throw away two weeks of work because a Tuesday call changed the framing. Watching a demo and saying *that's the wrong demo, here's why*. Mentoring a junior who just realised something that used to take five years.

This is the part that doesn't compress. You can compress the typing arbitrarily — and we have. You can't compress the deciding. You can only *parallelise* the deciding by adding people with different views, and you can only do that with a team.

Headcount might shift. Roles definitely shift. **What disappears is the role of the engineer-as-typist** — the version of the job that the dark-factory metaphor was secretly mocking. Nobody misses that role; it was never the part of the job that mattered.

What replaces it is the engineer who builds taste. Who reads customer evidence. Who argues with the PM at 11am because the framing is off. Who reviews the agent's PR not for syntax but for whether it's solving the right thing. The engineer who is *now responsible for direction*, because the typing got cheap.

## The team is the product surface now

Here's the punchline. The dark factory works in places where the product *is* the spec. Cars. Phones. Cookies. The spec doesn't argue with the line.

The product of a software team is *the next decision*. The decision compounds — the wrong call in March costs you six months in October. There's no "lights out" version of that loop, because the loop *is* the product.

The agent doesn't replace the team. It removes the part of the team's job that wasn't the team's job in the first place. What's left — judgment, taste, fresh angles, customer signal, the willingness to throw away a week — is the part that was always making the company work.

That part doesn't run in the dark.

The dark factory is closed. We're working on the lit one.
