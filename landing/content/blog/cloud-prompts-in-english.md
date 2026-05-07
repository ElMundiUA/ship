---
title: Translating cloud-prompts to English
slug: cloud-prompts-in-english
date: 2026-04-09
kicker: Field note
description: The prompts that came over from elmundi were partly Ukrainian. We sat one afternoon and translated them. Three things changed; one of them was unexpected.
reading_time: 3
author: Denys Kuzin
tags: [prompts, agents, build-in-public, field-note]
---

The prompts that landed in the first commit were bilingual. The role descriptions were English; the in-context examples were Ukrainian. Inside elmundi this had been fine — the team was Ukrainian, the customer was Ukrainian, the LLM happily switched.

For Ship to be a public project, the prompts had to switch too. We sat one afternoon and translated them. The commit body says *chore(prompts): translate cloud-prompts and related templates to English*. It was not a chore.

## What got translated

About forty prompt templates. Role definitions, exit-contract prose, error messages the agent would write back at the user. Plus the `_base` template that everything inherits from.

The translation was straightforward in the literal sense — DeepL and a careful re-read. The hard part was something else.

## Three things changed

**1. The voice settled.** Bilingual prompts had a small voice drift between the English parts and the Ukrainian parts. After translation, every role's prose came out in one consistent register. The agent's outputs got more uniform within hours of merging. We had assumed the bilingual mix was neutral; it wasn't.

**2. The model preferred the new prose.** Output quality on the same tickets noticeably improved — fewer hallucinations on names, fewer mixed-language responses, more confident citations. We were not running an A/B; this was just on-the-day-after observation, but several team members flagged it independently. Plausible mechanism: pretraining data is overwhelmingly English, and a fully-English prompt nudges the model into its "best" distribution.

**3. We discovered which lines we never actually needed.** Six templates had bilingual sections that were copy-pasted between roles for safety. When we translated and consolidated, three of those got deleted entirely — they hadn't been adding signal, just length. Translating forced a re-read; re-reading found the dead weight.

## What we kept bilingual

Two surfaces stayed bilingual on purpose:

- **User-facing chat in the console.** If the user writes in Ukrainian, the agent answers in Ukrainian. The prompt asks for *the user's language*, not English. (RFC-0006 added explicit-shift detection later, but the language pivot was already there.)
- **The book.** `landing/content/book.md` still has a Ukrainian Part II — the methodology section that originated in Ukrainian and was specifically requested by the original audience. We added it back as `docs(uk): add Ukrainian Part II — Prompts & workflows` two days later.

So: **English for the agent's instructions, the user's language for the agent's output.** Two different surfaces, two different policies.

## The lesson

Mixed-language prompts aren't neutral. They drift the voice, they leave dead lines, they nudge the model out of its strongest distribution. If your team works in language X but your prompts are read by a model trained on language Y — translate. The cost is one afternoon. The payoff is in every reply the agent gives, forever.
