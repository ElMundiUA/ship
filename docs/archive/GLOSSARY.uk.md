# Глосарій

**SDLC (розклад)** — Основна смуга постачання з `linear-agent-sdlc-scheduled.yml`: intake → clarification → BA → developer, одна роль на слот cron; pick лише з **Todo** у pre-release проєкті Linear (не з **Backlog**).

**Щоденні аудити** — Окремий розклад (`linear-agent-daily-audits.yml`): tech architect, QA architect, security officer. **Не** споживають чергу SDLC; пишуть у проєкти **tech debt** і **security** лише з доказами.

**Автономний цикл** — `linear-agent-autonomous.yml`: додаткова автоматизація з власним каденсом; не замінює SDLC.

**Workflow self-heal** — `workflow-self-heal.yml`: аналіз здоров’я пайплайну (спочатку звіт CLI), опційний Cloud Agent на налаштованому Linear issue.

**Pick-скрипти** — `tools/linear-agent/scripts/pick-*.mjs`: детермінований вибір щонайбільше одного issue за запуск (команда, колонка, проєкт, мітки).

**`cloud-agent-launch.mjs`** — Збирає промпти з `cloud-prompts/*.md` та `.cursor/skills`, викликає Cursor Cloud Agents API.

**linear-agent CLI** — `tools/linear-agent/dist/cli.js`: `start`, `get`, `init`, `next`, `pr-create` тощо.

**Backlog vs Todo (автоматизація)** — **Backlog** = лише тріаж людьми; SDLC не pick-ить звідти. **Todo** = контрольований вхід для автоматичних pick після промоуту та міток.

**`ready:developer`** — Мітка для gating developer pick (Todo + проєкт + ця мітка).

**Змінні `LINEAR_*`** — Secrets/variables GitHub для команди Linear, проєкту SDLC, аудит-проєктів тощо. Деталі в runbook.

**E2E** — End-to-end тести; у репо часто Playwright проти hosted dev (`e2e-regression-dev.yml`).

**MCP** — Model Context Protocol; у Cursor Automations для Linear.

**Сітка UTC** — Парні години для ролей SDLC (:10 / :25 / :40 / :55) і пов’язані розклади; канон у [SDLC (розклад)](SDLC-AUTOMATION-SETUP.md).
