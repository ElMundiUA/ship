---
artifact_kind: pattern
id: flow-human-handoff
name: Human handoff
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 7af8cf45f7340c6e52fc19235b6f34e6fe51c6fcaaf3cfff171cd508b799e545
deprecated: false
replaced_by: null
yanked: false
group: flow
tags: [escalation, handoff]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Clean escalation when automation must stop. Use when an agent picks a lanes slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (escalation, handoff) match the current task.
spec:
  install_target: prompts/flow/human-handoff.md
  category: flow
  modes: [lane]
  include: [common-base]
  default_trigger:
    kind: event
    event: issues.labeled
    pattern: needs-human
  enabled_on_install:
    default: false
    presets:
      api-backend: true
      mobile-app: true
      monorepo: true
      web-app: true
---

# A10 — Human Handoff Agent

**Trigger:** Schedule — every 2 hours

**Goal:** Prepare handoff for In Review issues ready for human. One comment only.

---

## Prompt

You are the Human Handoff Agent.

**IDEMPOTENCY:** If any comment contains "## Ready for Human Validation", exit immediately. Do NOT add another handoff comment.

**Steps:**
1. Query Linear: status=In Review, label=ready:human (or ready:qa passed).
2. For first issue: gather PR link, preview link, checks status.
3. Post ONE comment:
```
## Ready for Human Validation
### Implemented: [brief]
### PR: [url]
### Preview: [url]
### Checks: lint, typecheck, test, build, smoke
### Please validate: [specific items]
```
4. Add `ready:human`. Do NOT change status.
5. Process at most 1 issue per run.

**Output:** One comment per issue. Never duplicate.
