# Каталог GitHub workflow

**Призначення:** один індекс workflow, що стосуються Linear, агентів або якості релізу.  
**Аудиторія:** платформені інженери, супровід репозиторію.  
**Результат:** знайдете потрібний YAML, тригер і посилання на глибший гайд.

Для **сітки cron SDLC** (UTC, pick) див. **[SDLC (розклад)](sdlc-scheduled.md)** — канонічний опис розкладу.

| Файл workflow | Типовий тригер | Роль |
|---------------|----------------|------|
| `linear-agent-sdlc-scheduled.yml` | Cron (парні години UTC) + `workflow_dispatch` | Intake → clarification → BA → developer через pick + Cloud Agent |
| `workflow-self-heal.yml` | Cron (непарні години) + dispatch | Звіт CLI по пайплайну, опційний Cloud Agent на налаштованому issue |
| `linear-agent-autonomous.yml` | Cron (за замовч. кожні 6 год) + dispatch | Окремий автономний цикл; доповнює SDLC |
| `linear-agent-daily-audits.yml` | Щоденний cron + dispatch | Аудити tech / QA / security → окремі проєкти Linear |
| `linear-agent-release-check-on-deploy.yml` | Після успішного preview deploy | Автоматизація release-check |
| `check-failure-recovery.yml` | Падіння перевірок PR | Відновлення / follow-up |
| `pr-preview.yml` | Події PR | Playwright + preview deploy |
| `linear-agent-webhooks.yml` | Webhooks (опційно) | Інтеграції на кшталт A5/A6/A7 |
| `e2e-regression-dev.yml` | Розклад + ручний | Повний Playwright regression проти hosted dev — див. **[Pre-release та E2E](pre-release-e2e.md)** |
| `docker-build-push.yml` | Push у `main` | Збірка/push образу, оновлення dev — див. **Pre-release та E2E** |
| `bunny-promote-prod-*.yml` | `workflow_dispatch` | Прод-промоут з тегів Docker Hub — див. **Pre-release та E2E** |

**Пов’язано:** [Налаштування автономного пайплайну](operator-setup.md) (секрети, змінні, локальний дебаг), [Щоденні аудити](daily-audits.md), [Усунення несправностей](../../framework/index.md#when-things-break).
