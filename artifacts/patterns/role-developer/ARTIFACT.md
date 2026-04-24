---
artifact_kind: pattern
id: role-developer
name: Developer
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 635fe4b30f1f93186289c48757489df34626266da5a3f67fa587b609950f87a9
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

1. Linear status should already be **In Progress** (set by GitHub). The branch for this run is provided by the API as `**fix/{{ISSUE}}-auto**` — work **only** in that branch. Do not create `**feature/{{ISSUE}}-auto**` or duplicate work in a second branch: that leads to two PRs for one ticket and manual cleanup.
2. Implement per description and AC.
3. **Tests:** add or update **unit/integration** for new logic; if UX or a critical flow changes — update or add **e2e** (Playwright). Do not stop at a green `test` alone: new behaviour should be covered by checks; if not, explain clearly in the PR/Linear comment why (rare case).
4. `cd website`: run `npm run lint`, `typecheck`, `test`, `build`, `test:e2e:smoke` (chromium-desktop where applicable) — all relevant targets must pass before opening the PR.
5. Commit message: `fix({{ISSUE}}): …` or `feat({{ISSUE}}): …`
6. **Before opening a PR:** in GitHub check there is no **open** PR for this ticket already (body/title with `Closes {{ISSUE}}`, branch `fix/{{ISSUE}}-auto` or similar). If one exists — **do not open a second**: push to the existing branch or one Linear comment with the PR link.
7. Open **exactly one** PR with `Closes {{ISSUE}}` (if none open yet). After the PR — status **In Review** in Linear.

One ticket comment with the PR link (one per pass). End with: `[GitHub SDLC:developer]`
