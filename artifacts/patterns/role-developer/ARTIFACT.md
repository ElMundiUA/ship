---
artifact_kind: pattern
id: role-developer
name: Developer
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-05-03T15:00:00+00:00"
content_sha256: f659c60fc3dabee93dc475f9fc7c1af66097021bbc12baec9a2f6787ebdbeabe
deprecated: false
replaced_by: null
yanked: false
group: role
tags: [implementation, pr]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Implementation role: branch contract, PR shape, evidence. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (implementation, pr) match the current task.
category: planning_process
critical: false
spec:
  install_target: prompts/role/developer.md
  category: role
  modes: [lane, request]
  include: [common-base]
  inbox:
    profile: role_reviewer
  default_trigger:
    kind: event
    event: issues.labeled
    pattern: "ready:developer"
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
  role: developer
---

# Role: Developer ({{ISSUE}})

{{BASE}}

## Context

- **Title:** {{TITLE}}
- **Description:** {{DESCRIPTION}}

## Task

Linear status is already **In Progress** (set by GitHub). The API has provided the branch for this run as `fix/{{ISSUE}}-auto` — implement the change described above on that branch and open a PR.

The standing rules — branch contract, tests, lint/typecheck/test/build/e2e gates, commit message format, the "exactly one PR with `Closes {{ISSUE}}` and move to In Review" shape — come from your workspace's policies.

End your single ticket comment (with the PR link) with: `[GitHub SDLC:developer]`
