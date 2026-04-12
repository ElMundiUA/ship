# PR gate & preview

**Intent:** every change set passes through **CI truth** (build, unit, static checks) and, when policy demands it, a **hosted preview** with evidence attached to the PR or ticket.

## Invariants

- Red checks are **blocking** unless policy explicitly allows merge with waiver—and waiver is visible in the audit trail.
- Preview URLs are **stable enough** for reviewers; flaky infra is tracked as **infra debt**, not “ignored QA.”

## What you ship

- Required status checks on the default branch.
- Optional preview deploy workflow on PR synchronize.
- Marker pattern in PR bodies/comments so agents do not spam duplicates (see your org’s marker contract).

## Read next

- [Playwright](/tools/playwright) — hosted regression runner role.
- [Preview smoke check](/patterns/catalog-a7-preview-validation) — lane prompt intent.
- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — gate language.
