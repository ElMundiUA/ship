# Hosted E2E regression

**Intent:** prove behaviour against a **real URL** (dev/stage) on a schedule and on demand—not only pre-merge unit tests.

## Invariants

- Tests target **pinned base URLs** and known auth strategy; “works on my laptop” is not the bar.
- Failures produce **artifacts** (trace, HTML report) referenced from the ticket or PR.

## What you ship

- A workflow that runs the E2E suite against **hosted dev** (scheduled + `workflow_dispatch`).
- Separate lane from **SDLC picks** so a red suite does not masquerade as “intake failed.”

## Read next

- [Playwright](/tools/playwright).
- [Acceptance verification](/patterns/catalog-a9-qa).
- [Examples → ElMundi](/docs/examples/elmundi) — reference wiring for E2E jobs.
