# Фази впровадження (rollout)

**Призначення:** зменшити ризик **big bang**.  
**Аудиторія:** керівництво інженерії, платформа.

## Фаза 0 — Готовність

- Переконатися, що [Бачення та масштабування](enterprise.md) відповідає апетиту до ризику.
- Узгодити структуру проєктів Linear і політику міток ([Глосарій](GLOSSARY.md)).
- Security: питання з [Security brief](security-brief.md).

## Фаза 1 — Пілот (одна команда / проєкт)

- Увімкнути **SDLC scheduled** для **одного** pre-release проєкту.
- Щоденні аудити вимкнено або в read-only, поки SDLC стабільний.
- Критерій успіху: передбачувані переходи **Todo → In Progress** з аудитованими коментарями; без сюрпризів з **Backlog**.

## Фаза 2 — Розширення

- Додати **щоденні аудити** з окремими проєктами Linear.
- За потреби — **workflow self-heal** ([Каталог workflow](WORKFLOWS-CATALOG.md)).

## Фаза 3 — Загартування

- **E2E regression** на hosted dev у чеклисті релізу ([Pre-release та E2E](PRE-RELEASE-DEPLOY-E2E.md)).
- Налаштування пропускної здатності cron vs ліміти Cursor ([SDLC (розклад)](SDLC-AUTOMATION-SETUP.md)).

## Фаза 4 — Опційні експерименти

- Оцінити **Cursor Automations** vs GitHub-оркестрація ([міграція](CURSOR-AUTOMATIONS-MIGRATION.md)).

**Governance:** [Governance та RACI](governance-raci.md).
