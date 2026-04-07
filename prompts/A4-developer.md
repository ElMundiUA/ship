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
