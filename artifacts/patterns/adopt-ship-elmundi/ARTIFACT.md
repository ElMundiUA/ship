---
artifact_kind: pattern
id: adopt-ship-elmundi
name: Adopt Ship (reference org)
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: dc3412459176fd3ec8788f7154308d57c8d0e347073cd0c9b0c61395982ebfc0
deprecated: false
replaced_by: null
yanked: false
group: onboarding
tags: [onboarding, reference-org]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Queue names, digest/retro, QA-first habits; apply after the baseline playbook. Use when an agent picks a onboarding slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (onboarding, reference-org) match the current task.
spec:
  install_target: prompts/onboarding/adopt-ship-elmundi.md
---

# ElMundi addendum (for instruction-first adoption)

Apply after `adopt-ship-generic.md`.

Target context: ElMundi-style monorepo (`website/`, `.github/workflows/`, delivery + audit lanes).

## ElMundi-specific expectations

- Queue column canonical name: `Todo`.
- Delivery and audit lanes stay separate.
- QA verifies behavior first; QA automation encodes it into reusable tests.
- Daily digest and daily retro emails are required (DL recommended).

## Naming conventions

- Historical names like `linear-agent-*.yml` may remain in repo history; normalize to neutral naming where feasible.
- Preserve one canonical branch naming rule per ticket to avoid duplicate PRs.
- Keep ElMundi as a reference implementation page in docs and update it when process changes.

## Verification targets

1. Morning digest definition present.
2. End-of-day retro recommendations definition present.
3. Weekly (or policy-defined) prod promotion gate linked to regression evidence.
4. Human merge ownership remains explicit.
