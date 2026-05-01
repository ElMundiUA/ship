# The Ship manual

This is the user manual: how to set up and operate Ship as a product delivery workspace, written in human language. It covers everything the product does today — from a non-technical product owner who needs a friendly explainer of what an "OpenAI key" is, to an engineer wiring `shipctl` into CI.

The manual is one of three reading surfaces. The [book](/book) is the long argument for why the method exists. [Use cases](/use-cases) show the public deployments. This manual is the operator's reference for the product as it works today.

## How the manual is laid out

The chapters read in order, and each chapter is one short essay on one idea — three to six minutes at a normal pace. You can read straight through, or jump to whichever part matches the current question.

- **[Orientation](/docs/orientation/what-is-ship)** — what Ship is, the seven words you'll meet on every screen, what a normal morning looks like.
- **[Setup](/docs/setup/onboarding-wizard)** — the onboarding wizard, the GitHub App, binding the tracker, members and roles, integrations beyond the tracker.
- **[Knowledge](/docs/knowledge/overview)** — what knowledge is for, buckets, importing, the distiller and review path, chat as a knowledge tool.
- **[Inbox](/docs/inbox/overview)** — decision work, the five item types, routing rules, the disposition vocabulary.
- **[Process](/docs/process/overview)** — the model (process / states / routines / specialists), the editor, the routine catalogue, tracker mapping, healthy vs unhealthy routines.
- **[Operating](/docs/operating/morning-loop)** — the morning loop, the audit log, the discipline of reading silent failures.
- **[Policies, secrets, evidence](/docs/policies/policies)** — workspace-wide rules, where secrets live, the evidence checklist.
- **[Local repo](/docs/developer/ship-folder)** — `.ship/`, `shipctl`, authoring custom artefacts, applying bundle updates.
- **[Reference](/docs/reference/cli)** — `shipctl` command catalogue, troubleshooting (symptom → cause → fix), glossary.
- **[Appendix](/docs/appendix)** — friendly per-entry explainers for non-technical readers. The wizard cross-links straight to the relevant entry; you can also browse the page directly.

The [implementation spec](/docs/discovery), the [protocol RFCs](/docs/protocol), and the [authoring reference](/docs/authoring) are kept separately for engineers maintaining the catalogue. The [roadmap](/roadmap) describes what Ship ships at production depth today and what we are growing toward — that page lives on the marketing site, not in `/docs`.

## What stays true from the book

The manual uses simpler words than the book, but it keeps the same spine:

- Humans own intent. Ship can move work, but it does not own the product decision.
- Legibility is kindness. A future reviewer should understand what ran, why, and where the proof lives.
- Quiet systems beat loud demos. The goal is controlled delivery, not a fireworks show of agent activity.
- Evidence beats opinion. Each important action should point to a ticket, PR, check, comment, or knowledge article.
- Fences beat exhortations. Machines need clear allowed scopes, states, owners, and secrets.
- Vendors are plugs, not gods. Tracker, CI, model, and agent host can change while the story stays stable.

## Where to start

A new reader: open [What Ship is](/docs/orientation/what-is-ship) and read Orientation in order, then [Setup](/docs/setup/onboarding-wizard).

An operator already running Ship: skim [Vocabulary](/docs/orientation/vocabulary) for the terms, then jump to whichever part matches your current question — most often [Inbox](/docs/inbox/overview), [Process](/docs/process/overview), or [Operating](/docs/operating/morning-loop).

An engineer wiring a repo: start at [the `.ship/` folder](/docs/developer/ship-folder) and [shipctl](/docs/developer/shipctl). The reference and CLI catalogue are next door.

A non-technical product owner stuck on a wizard step: follow the link the wizard gives you — it deep-links straight into the [Appendix](/docs/appendix).
