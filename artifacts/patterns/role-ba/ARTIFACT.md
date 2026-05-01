---
artifact_kind: pattern
id: role-ba
name: BA / specification
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: ee5f5cc3ec7e9f8b9cec1156381cd72bc14fec9dd8318e66779fa93005df0da0
deprecated: false
replaced_by: null
yanked: false
group: role
tags: [ba, spec]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Specification and handoff quality before implementation picks. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (ba, spec) match the current task.
category: planning_process
critical: false
spec:
  install_target: prompts/role/ba.md
  category: role
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: role_reviewer
  default_trigger:
    kind: event
    event: issues.labeled
    pattern: "ready:ba"
  inputs:
    - name: issue_url
      type: url
      required: true
      hint: "Issue URL"
    - name: depth
      type: enum
      values: [quick, thorough]
      default: thorough
  enabled_on_install:
    default: false
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
  template: true
  role: ba
---

# Role: BA / Spec ({{ISSUE}})

{{BASE}}

## Context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

The ticket arrives shaped by intake (Problem / Goal / Expected behaviour / Scope / AC / Non-goals / Risks). Your job is to **extend the description** with a specification depth that the developer can implement against — not to file a new comment.

1. Finish with `outcome=ready_next_step`, `stage_next=dev_implementation`. **Rewrite the ticket body** via the `description` field on the finish payload — the server replaces the tracker description (Linear keeps the prior body in history). Keep the intake sections (Problem / Goal / Expected behaviour / Scope / AC / Non-goals / Risks) unchanged where they're correct; tighten where they aren't. Below them, append the BA spec:
   - **Feature description** — one paragraph in your own words.
   - **User stories** — `As a … I want … so that …` bullets, one per scenario.
   - **Acceptance criteria** — Given/When/Then or numbered list, observable + testable.
   - **Edge cases** — explicit list, with the expected behaviour for each.
   - **Impacted components** — repo paths / modules / services.
   - **Technical notes** — schema/API/UX hooks the developer needs but the user shouldn't have to derive.
   - **Test plan** — what the QA stage will exercise.
2. If scope is too large for one delivery: finish with `outcome=needs_clarification` and propose a split in the `comment`. Do not set `stage_next=dev_implementation` until a human confirms the slice.

The `comment` field on this stage is a one-paragraph audit narration of what you added/changed and why (for the activity feed). End it with: `[Ship SDLC:role-ba]`
