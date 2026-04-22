---
artifact_kind: pattern
id: op-workflow-self-heal
name: Workflow self-heal
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 157289b38400a5dc6684fc254c79a1ba559c69b221c1e97d10842224efc05286
deprecated: false
replaced_by: null
yanked: false
group: op
tags: [ci, drift]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Repair signals from CI and config drift without colliding with intake. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (ci, drift) match the current task.
spec:
  install_target: prompts/op/workflow-self-heal.md
  category: op
  modes: [lane]
  include: [common-base]
  lane_id: self_heal
  lane_name: "Pipeline self-heal"
  lane_summary: >-
    Nightly sweep of Ship-owned workflows — re-runs flaky CI, opens a PR when a starter template drifts.
  default_trigger:
    kind: schedule
    cron: "0 4 * * *"
  enabled_on_install:
    default: false
    presets:
      monorepo: true
      web-app: true
  template: true
  role: workflow-self-heal
---

# Role: Workflow self-heal / pipeline autofix ({{ISSUE}})

{{BASE}}

## Linear ticket context (notifications)

Use **{{ISSUE}}** only for **one** short status comment after a successful pass (after the PR). Do not inflate the ticket description.

- **Title:** {{TITLE}}
- **Description (excerpt):** {{DESCRIPTION}}

## Task

1. A **JSON report** from `workflow-self-healer` (`issues`, `recommendations`) may be attached below in the prompt. Rely on facts and on the **workflow-self-healer** skill.
2. If the report is empty or issues are already fixed on `**main**` — **exit with no changes, no PR, and no Linear comment**.
3. Otherwise apply **minimal** fixes; run locally what makes sense (lint/test/targeted script).

## Anti-duplication (required)

Before any commits/PR:

1. **Open PRs:** check GitHub for open PRs related to the same symptom (branches `cursor/workflow-self-heal-*`, titles/changelog mentioning preview/probe/Linear/self-heal). If a PR already exists with the same fix or overlapping scope — **do not create a second**: comment on the existing PR or **stop** with one short Linear note that work is already in PR #N.
2. **Linear comments:** read the latest comments on **{{ISSUE}}**. If there is already a comment with `[GitHub SDLC:workflow-self-heal]` linking to an open PR from the last **72 hours** and that PR is still open — **do not** post a duplicate or open a parallel PR without new facts in the report.
3. **One outcome per pass:** at most **one** new PR from this run. Do not split one fix across multiple PRs.
4. **Developer/SDLC branches:** do not collide with other active `fix/*-auto` SDLC branches or rename others’ artifacts.

After successful work:

1. One PR with a clear description. In Linear — **at most one** new comment: PR link + `[GitHub SDLC:workflow-self-heal]`.

## Do not

- Do not touch GitHub secrets / variables in code.
- Do not merge PRs.
- Do not spam Linear (no chains of “another update” without a new PR or new finding in the report).
