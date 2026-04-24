---
artifact_kind: pattern
id: onboard-seed-knowledge
name: Seed .ship/knowledge starter buckets
version: 1.0.0
channel: stable
min_shipctl: 0.12.0
updated_at: "2026-04-21T14:00:00+00:00"
content_sha256: bd3d8fd2389e9477c5a366e8c99c2895c017da1f78de144e91f277a90167eeaf
deprecated: false
replaced_by: null
yanked: false
group: onboard
tags: [seed, once, knowledge, onboarding]
authors: ["@elmundi/ship-core"]
license: Apache-2.0
description: >-
  First-time workspace seed for `.ship/knowledge/`. Drops two starter
  markdown buckets — `code-style.md` and `ui-runbook.md` — so agents
  running Ship lanes have a non-empty knowledge base on day zero.
  Wired as a `kind=once` lane in `.ship/config.yml` (RFC-0007); the
  agent runs once, writes the files, opens a PR, and `shipctl run`
  records the idempotency marker so subsequent invocations no-op.
category: knowledge_docs
critical: false
spec:
  install_target: prompts/onboard/seed-knowledge.md
  category: onboard
  modes: [request]
  inbox:
    profile: onboarding
  template: true
---

## Ship · seed knowledge starters

You are executing the **one-shot knowledge seeding lane** for this
repository. Your job is to create the two starter knowledge buckets
under `.ship/knowledge/` so subsequent Ship lanes have a non-empty
knowledge base to consult.

### Files to create

If a file already exists, **do not overwrite it** — leave a human's
curation alone. Log which files you skipped and why.

1. `.ship/knowledge/code-style.md` — languages in use, naming
   conventions, import ordering, test layout, review checklist. Seed
   from `artifacts/knowledge-starters/code-style.md` in the Ship
   monorepo or fetch via `shipctl docs fetch artifacts/knowledge-starters/code-style.md`.

2. `.ship/knowledge/ui-runbook.md` — design-system usage, component
   states, perf budgets, accessibility floor. Seed from
   `artifacts/knowledge-starters/ui-runbook.md` (same fetch path).

### Discipline

- **One PR for the whole seeding step.** Branch name:
  `ship/onboard-seed-knowledge`. Title: `ship: seed .ship/knowledge
  starter buckets`. Body: one-paragraph summary + links to the
  upstream `artifacts/knowledge-starters/*` for traceability.
- **Idempotency.** `shipctl run --lane seed_knowledge_starters` has
  already checked the marker under `.ship/state/`. If you find yourself
  asked to re-seed, stop and raise the issue in the PR — something is
  wrong with the marker.
- **Do not hand-author the content.** Use the upstream starter files
  verbatim as the initial commit. The operator will edit them on-repo
  afterwards; that edit is what drives the marker to invalidate on the
  next pattern bump.
- **Commit the marker**. After the files land, the workflow wrapper
  will commit `.ship/state/onboard-seed-knowledge.v1.json` in the
  same PR. Don't move or rename that file.

### Ending the lane

When the PR is open (or already present — same idempotent outcome):
- Print a one-line summary for the callback: `seeded X/2 buckets
  (opened PR #NNN)`.
- Exit the agent conversation. `shipctl run` will call back with
  `status=ok`.

### Failure modes

- **Starter files missing upstream.** Stop. Do not commit placeholder
  content. Report `status=failed` with the missing path.
- **Branch already present.** Reuse it; do not open a parallel PR.
- **Files present on disk but marker missing.** Create the marker;
  do not touch the files. Note the reconciliation in the PR body.

### Global rules

- Never merge the PR yourself; human review is required.
- Never edit `.ship/config.yml` from this lane.
