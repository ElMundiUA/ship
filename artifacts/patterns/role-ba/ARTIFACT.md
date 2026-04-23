---
artifact_kind: pattern
id: role-ba
name: BA / specification
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 97500634699597880d1bb6ebc2cacc9846b56223fba31fdeaa00a8eea53338a1
deprecated: false
replaced_by: null
yanked: false
group: role
tags: [ba, spec]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Specification and handoff quality before implementation picks. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (ba, spec) match the current task.
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

1. Add specification: Feature Description, User Stories, AC, Edge Cases, Impacted Components, Technical Notes, Test Plan.
2. If scope is huge — create sub-issues and **do not** set `ready:developer` without review.
3. Otherwise: `ready:developer`, status **Todo**.

One short summary comment. End with: `[GitHub SDLC:ba]`
