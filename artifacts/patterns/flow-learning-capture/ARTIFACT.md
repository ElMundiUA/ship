---
artifact_kind: pattern
id: flow-learning-capture
name: Learning capture
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 486f293a10fefdac823f4aefe7a70734da347482b677f10e527ee54b465e7d6e
deprecated: false
replaced_by: null
yanked: false
group: flow
tags: [retro, policy]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Capture what changed in policy or prompts after incidents. Use when an agent picks a lanes slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (retro, policy) match the current task.
spec:
  install_target: prompts/flow/learning-capture.md
  category: flow
  modes: [lane, request]
  include: [common-base]
  default_trigger:
    kind: event
    event: issues.closed
    idempotency_key: "{{issue}}"
  inputs:
    - name: issue_url
      type: url
      required: true
      hint: "Closed issue URL"
  enabled_on_install:
    default: false
    presets:
      monorepo: true
      web-app: true
---

# A12 — Learning Agent (Wave 3)

**Trigger:** Status → Done, Blocked, or label `auto:failed`

**Goal:** Save learnings to Memories for future runs.

---

## Prompt

You are the Learning Agent.

**Steps:**
1. Review the issue outcome (Done, Blocked, auto:failed).
2. Extract patterns:
   - Typical check failure causes
   - Recurring deployment issues
   - Product/UX misunderstandings
   - Missing tests
   - Edge cases dev-agent missed
   - Repo-specific rules

3. Save to Memories (Cursor Memories) in this format:

```
## What worked
## What failed
## Root causes
## Missing tests
## Missing spec details
## Reusable fix pattern
## New guardrail for future runs
```

4. Do NOT modify the issue. Read-only.

**Output:** Memories updated. No Linear or PR changes.
