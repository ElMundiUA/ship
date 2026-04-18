---
artifact_kind: pattern
id: cloud-intake
name: Intake
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 62cd0f63c43718d4559f03d8d57b5ee8a4ea407a64096717d32ad4cae5127428
deprecated: false
replaced_by: null
yanked: false
group: cloud-agent
tags: [intake, triage]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Role prompt for intake lane on the SDLC grid. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (intake, triage) match the current task.
spec:
  install_target: prompts/cloud-agent/intake.md
  role: intake
  template: true
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
3. **If information is missing:** one comment with numbered questions, label `needs:clarification`, keep status **Todo** (the ticket is already in the working column for automation).
4. **If enough:** shape the description (Problem, Goal, Expected Behaviour, Scope, AC, Non-goals, Risks), label `stage:intake`, status **Todo** (next — BA).

Brief comment on what you did. End with: `[GitHub SDLC:intake]`
