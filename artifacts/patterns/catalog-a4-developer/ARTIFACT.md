---
artifact_kind: pattern
id: catalog-a4-developer
name: Developer execution
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 1a8365f7dcf43ab19fe44c08e980f9efb58d2069f7f0d8023c290e29e24b5cfb
deprecated: false
replaced_by: null
yanked: false
group: lanes
tags: [implementation, pick-window]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Developer execution pattern aligned to pick windows. Use when an agent picks a lanes slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (implementation, pick-window) match the current task.
spec:
  install_target: prompts/catalog/A4-developer.md
---

# A4 — Developer Agent (Cursor — только если GitHub отключён)

**Primary:** GitHub → `linear-agent-sdlc-scheduled.yml` (cron `0 */2 * * *`) → `pick-next-dev-issue.mjs` → Cloud Agent. **Немає тикета — крок Launch не виконується.**

**Cursor:** Отключи Schedule для A4, чтобы не дублировать GitHub.

If you still run this automation manually:

**Steps:**
1. Query Linear: status=Todo, label=ready:developer, no human:review-required / auto:failed.
2. Pick oldest by updatedAt. If none, **exit without comment**.
3. Move to **In Progress**, add `stage:dev`, implement, PR, move to **In Review**.
4. One issue per run.

**Global rules:** Never merge PRs. Never mark Done without human approval.
