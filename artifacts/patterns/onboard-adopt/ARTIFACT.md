---
artifact_kind: pattern
id: onboard-adopt
name: Adopt Ship (baseline)
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: 887a30f31c2d1dfbcbb67f1b0d87c4cfe7c9007fdda03674e6da8bd28080503e
deprecated: false
replaced_by: null
yanked: false
group: onboard
tags: [onboarding, adoption]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Primary onboarding playbook before any org-specific addendum. Use when an agent picks a onboarding slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (onboarding, adoption) match the current task.
spec:
  install_target: prompts/onboard/adopt.md
  category: onboard
  modes: [request]
  inbox:
    profile: onboarding
---

# Ship — interactive adoption playbook (instruction-first)

You are a coding agent integrating Ship methodology into the current repository.

## Mandatory mode

Start with **interactive discovery**. Do not assume tracker, CI, deployment, or release policy.

Ask for or infer with confirmation:
1. Tracker system and current workflow states.
2. CI/scheduler system and trigger strategy.
3. Agent runtime preference (Cursor/Codex/Claude/etc.).
4. Quality gate expectations (manual QA, QA automation, regression policy).
5. Release policy (manual vs scheduled promote).
6. Daily digest/retro recipients (recommend DL aliases).

If uncertain, present 1-2 options and request a decision before implementation.

## Outcomes (definition of done)

1. Repository has a documented Ship adaptation plan (states, labels/fields, evidence policy).
2. `getting-started` style runbook exists in target repo with exact commands/entrypoints for that stack.
3. At least one automation path is wired (or explicitly documented as intentionally manual).
4. Daily digest + daily retro workflow policy is documented (email targets configured by user).
5. PR includes "Adoption notes" with:
   - chosen tracker mapping,
   - CI/scheduler mapping,
   - quality/release gates,
   - follow-up tasks.

## Guardrails

- Never commit secrets/tokens.
- Do not remove existing production workflows without approval.
- Keep changes minimal and auditable.
- Prefer vendor-neutral interfaces over vendor-specific jargon.

## Recommended artifacts to create/update in target repo

- `docs/ship-adaptation.md` (or equivalent)
- `.env.example` entries for chosen stack
- one workflow/runbook proving first green path
- concise rollback notes for automation changes
