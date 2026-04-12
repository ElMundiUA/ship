# Playwright (regression runner)

**Role in Ship:** the **regression runner** capability — prove behaviour against a **hosted** URL (dev/stage), not only localhost mocks.

## What “good” looks like

- Tests run in CI (scheduled and/or on PR) with a **pinned base URL** and stable auth strategy (test users, storage state, or org-approved shortcuts).
- Failures produce **actionable artifacts** (trace, screenshot, HTML report) attached or linked from the tracker comment — not only “e2e red”.
- Flake policy: distinguish **product defect**, **environment drift**, and **test debt** (see catalog prompts for preview/E2E lanes).

## How Ship talks about it

- Hosted E2E is a **first-class gate** in delivery-quality docs: automated tests should track **accepted** behaviour.
- Prompt catalog includes preview validation and QA patterns; agents should read those before inventing new check shapes.

## Read next

- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — where automated regression evidence sits in the gate story.
- [Prompts & workflows — catalog](/docs/prompts-workflows) — search for preview/E2E intent sections.
- [Examples → ElMundi](/docs/examples/elmundi) — reference wiring for scheduled regression vs PR preview (workflow names differ per org).
