---
artifact_kind: pattern
id: role-clarification
name: Clarification
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-05-03T15:00:00+00:00"
content_sha256: 49f6a14f4bf71eba573122b03673195eba0dfa959b30a0859e57432a0927a8a9
deprecated: false
replaced_by: null
yanked: false
group: role
tags: [clarification, requirements]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Structured follow-ups when requirements are incomplete. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (clarification, requirements) match the current task.
category: incident_response
critical: false
spec:
  install_target: prompts/role/clarification.md
  category: role
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: flow_incident
  default_trigger:
    kind: event
    event: issues.labeled
    pattern: needs-clarification
  inputs:
    - name: issue_url
      type: url
      required: true
      hint: "Issue URL"
  enabled_on_install:
    default: false
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
  template: true
  role: clarification
---

# Role: Clarification follow-up ({{ISSUE}})

{{BASE}}

## Context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

Read comments (newest first). If the human resolved the questions: update the description, remove `needs:clarification`, ensure `stage:intake`, status **Todo**. If questions remain, post a short follow-up comment.

The standing rule — no-op while waiting on the human, one comment per pass — comes from your workspace's policies.

End with: `[GitHub SDLC:clarification]`
