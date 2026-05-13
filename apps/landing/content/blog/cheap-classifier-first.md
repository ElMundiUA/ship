---
title: The cheap classifier goes first
slug: cheap-classifier-first
date: 2026-05-02
kicker: Pattern
description: We were paying for an LLM call every turn to detect topic shifts that the user had already announced in plain language. Adding a fifteen-line regex in front of the classifier removed the cost and made the UX more decisive at the same time. The rule generalises.
reading_time: 4
author: Denys Kuzin
tags: [agents, classifiers, latency, build-in-public]
---

A topic-shift classifier is a thing you build the moment your single-window chat starts caring about long sessions. Ours sat between the user message arriving and the prompt being assembled — a fast-model call asking, in JSON, "is this a new topic?" If yes, the UI surfaces a banner offering to pack and start fresh.

It worked. It was also wasteful.

About a third of the user messages that triggered a "yes" came in shapes like *"let's switch topics"* or *"теперь другое"* or *"new topic"*. Things the user had already said, in words, in the message itself. We were spending an LLM call to discover content that was sitting there in plain text.

So we put a regex in front of the classifier.

## The thing the regex catches

There's a closed set of phrases — fifteen for our two-language audience — that mean "I am switching topics" with no ambiguity. They're surprisingly stable across users. *"forget that"*, *"never mind"*, *"moving on"*, *"забей"*, *"сменим тему"*. We compiled them into a regex matrix, anchored on word boundaries so *"switch to dark mode"* doesn't match *"switch"*, and ran it before the classifier:

```python
def detect_explicit_shift(message: str) -> bool:
    return any(p.search(message) for p in _EXPLICIT_SHIFT_PHRASES)
```

In `classify_shift`, the very first thing we do after the "have we got enough turns to even decide" guard is check the regex. If it fires, we return `shifted=True` with a flag saying "the user told us so" — and the LLM call never happens.

Costs we removed in the matched-case branch: the prompt assembly, the network round-trip to the fast model, the JSON parse, the "Anthropic doesn't honor `response_format` so try-the-salvage-fallback" branch. Time we removed: ~200ms median on the fast model.

That's the boring part. The interesting part is what we got back in UX.

## What the LLM was hedging

The classifier is conservative on purpose. We want it to err on "no shift" when uncertain — false positives are how you train users to ignore the banner. Conservatism plus 200ms of latency means the banner shows up with phrasing like *"Topic shift. Looks like you moved on. Pack this thread into a new bucket?"*. It hedges. It hedges *because the LLM doesn't know whether the user meant to switch or just changed the subject in passing.*

When we know the user said the words *"let's switch"*, hedging is wrong. The right copy is **"Switching topics. Got it — let's pack the current thread before we move on. Switch now."** No hedge, decisive verb. The classifier-driven banner reads like a system asking permission. The regex-driven banner reads like a system that listened.

We pipe the `explicit_phrase` flag through the SSE stream so the frontend can pick the right copy:

```python
TopicShiftDecision(
    shifted=True,
    reason="You said you're switching topics.",
    new_title=None,
    explicit_phrase=True,
)
```

The frontend doesn't need a parallel UX surface; it just varies the banner headline and CTA text. One bit of signal, two registers.

## The rule

There's a class of classifiers in agent loops that fits this shape. The user is doing something explicit, in language. You're paying an LLM to detect what the user already announced.

The pattern, named: **the cheap classifier goes first**.

Concretely: before any expensive model call whose output is a small categorical, run the fastest possible filter — a regex, a substring match, a keyword list — against the input. If the cheap filter is high-precision, treat it as authoritative and short-circuit. If it doesn't match, fall through to the expensive model.

You want high *precision* on the cheap filter, not high recall. The recall belongs to the expensive model. The cheap filter exists only to skim off the obvious cases and the cases where hedging would hurt.

A few examples we now look for in our own code:

- **Intent detection.** *"open the inbox"* / *"show me my inbox"* / *"what's on my plate"* are inbox-list intent. We currently send all of them to a model. We probably shouldn't.
- **Sentiment.** Explicit *"thanks"* / *"this is great"* / *"this is broken"* don't need a sentiment model.
- **Language.** A leading Cyrillic character means the message is Russian; we don't need a detector for that.
- **Confirmation.** *"yes"* / *"да"* / *"go"* / *"do it"* in response to a `ship-choice` widget is a button click in disguise.

In every case, the regex catches the obvious 30% and the model catches the long tail. The regex doesn't replace the model. It saves the model from having to think about cases the user already labelled.

## Two things to be careful about

**Anchor your patterns.** *"switch"* will match *"switch to dark mode"*. Word boundaries (`\b...\b`) and explicit verb-noun pairs (*"switch topics"*, not *"switch"*) keep precision high. We have one test case in our suite that exists just to make sure *"switch to dark mode"* doesn't trigger a topic shift.

**Don't let the cheap filter become the canon.** The expensive model still runs on everything that doesn't match. The temptation, after a regex starts catching most cases, is to keep adding entries instead of fixing the model. That ratchet ends with a 400-line regex that nobody can audit and a model that never improves. The regex is for the cases where language is unambiguous. Everything ambiguous is the model's job.

The user said *"let's switch."* You don't need a Sonnet call for that.
