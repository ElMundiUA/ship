---
artifact_kind: pattern
id: cloud-clarification
name: Clarification
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: cea269d645a0d57a3c3202483ceee41d72a568349b66cf908c3f42764bb080be
deprecated: false
replaced_by: null
yanked: false
group: cloud-agent
tags: [clarification, requirements]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Structured follow-ups when requirements are incomplete. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (clarification, requirements) match the current task.
spec:
  install_target: prompts/cloud-agent/clarification.md
  role: clarification
  template: true
---

# Role: Clarification follow-up ({{ISSUE}})

{{BASE}}

## Context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

1. Read comments (newest first). If the latest reply is from the agent and the human has not yet answered the questions — **do nothing**.
2. If the human resolved the questions: update the description, remove `needs:clarification`, ensure `stage:intake`, status **Todo**.
3. If questions remain — one short follow-up comment.

One pass — at most one comment when needed. End with: `[GitHub SDLC:clarification]`
