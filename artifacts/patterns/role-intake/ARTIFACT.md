---
artifact_kind: pattern
id: role-intake
name: Intake
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-05-03T15:00:00+00:00"
content_sha256: 213e4a40cd994f2587165279fe5cb4b2a3a9591afb15b46b80583abd56adb3ca
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

Classify the ticket: feature / bug / refactor / infra / improvement. Check completeness: goal, problem, expectation, AC, constraints.

**If enough to shape:** finish with `outcome=ready_next_step`, `stage_next=ba_requirements`, and rewrite the description (via the `description` field) using these sections in order:

1. **Problem**
2. **Goal**
3. **Expected behaviour**
4. **Scope**
5. **Acceptance criteria**
6. **Non-goals**
7. **Risks**

The standing rules — don't touch Backlog tickets, write the rewritten body to `description` (not `comment`), escalate as `needs_clarification` when context is missing — come from your workspace's policies.

The `comment` field carries a one-paragraph audit narration of *what you changed and why*. End it with: `[Ship SDLC:role-intake]`
