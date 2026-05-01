---
artifact_kind: pattern
id: role-intake
name: Intake
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 5aa31a6668412eb43417b374657fdbc3825f77e8baeed5fc5326079d411482f8
deprecated: false
replaced_by: null
yanked: false
group: role
tags: [intake, triage]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Role prompt for intake lane on the SDLC grid. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (intake, triage) match the current task.
category: planning_process
critical: false
spec:
  install_target: prompts/role/intake.md
  category: role
  modes: [lane, request]
  include: [common-base]
  # E14: tracker stage this pattern operates on. ``shipctl run``
  # uses this to pick the next eligible ticket via ``GET /tracker/next``.
  # Aligns with ``services/linear_provisioner.SHIP_FSM_STAGES`` so the
  # adapter can translate to a Linear filter without an extra map.
  fsm_stage: task_intake
  inbox:
    profile: role_reviewer
    overrides:
      clarification:
        handle: requested_by
  default_trigger:
    kind: event
    event: "issues.opened,reopened"
  inputs:
    - name: issue_url
      type: url
      required: true
      hint: "Issue URL"
  enabled_on_install:
    default: true
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
  template: true
  role: intake
---

# Role: Intake ({{ISSUE}})

{{BASE}}

## Ticket context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

The ticket is already in **Todo** and in the pre-release project — that means automation may pick it up (do not touch Backlog).

1. Classify: feature / bug / refactor / infra / improvement.
2. Check completeness: goal, problem, expectation, AC, constraints.
3. **If information is missing:** finish with `outcome=needs_clarification`. The `comment` carries the numbered questions; the server applies `needs:clarification` automatically.
4. **If enough to shape:** finish with `outcome=ready_next_step`, `stage_next=ba_requirements`. **Rewrite the ticket body** by setting the `description` field on the finish payload — the server replaces the tracker description (Linear keeps the prior body in history). Sections, in order: **Problem**, **Goal**, **Expected behaviour**, **Scope**, **Acceptance criteria**, **Non-goals**, **Risks**. Use the operator's original wording where it's already clear; tighten/restructure where it isn't. Do not paste the rewritten spec into the `comment` — that's what `description` is for.

The `comment` field on this stage carries a one-paragraph audit narration of *what you changed and why* (for the activity feed). End it with: `[Ship SDLC:role-intake]`
