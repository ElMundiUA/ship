# ElMundi — референсний деплой

Ця глава — **не** теорія Ship. Це **схема проводки** для однієї організації — **ElMundi** — яка реалізувала Ship у публічному монорепо (`website/`, `tools/linear-agent/`, `.github/workflows/`).

Якщо ти тут вперше, спочатку прочитай **[Framework](../../framework/index.md)** (одна довга сторінка — скроль). Повертайся сюди, коли потрібні **імена файлів**, **хвилини cron** і **домени**, а не філософія.

---

## Як Ship відображається на ElMundi (одна таблиця)

| Концепт Ship (framework) | Де це в ElMundi (examples) |
|----------------------------|----------------------------|
| **Трекер як джерело істини** | Linear — проєкти, стани, мітки; [SDLC (розклад)](sdlc-scheduled.md). |
| **Планувальник / годинник** | GitHub Actions — cron і `workflow_dispatch`; повна таблиця в [Каталозі workflow](workflows-catalog.md). |
| **Детермінований pick** | Node-скрипти в `tools/linear-agent/scripts/` — виклик з workflow; деталі в SDLC та operator docs. |
| **Версійовані промпти** | `tools/linear-agent/cloud-prompts/*.md` — одна роль на файл; [Каталог промптів](../../prompts-workflows/prompt-catalog.md). |
| **Запуск агента** | `cloud-agent-launch.mjs` + Cursor Cloud Agent API — секрети в [Налаштуванні оператора](operator-setup.md) та [Tools → Cursor Cloud Agent](../../tools/cursor-cloud-agent.md). |
| **Сітка доставки** | `linear-agent-sdlc-scheduled.yml` — хвилини та ролі в [SDLC (розклад)](sdlc-scheduled.md). |
| **Петля аудиту (окрема дошка)** | `linear-agent-daily-audits.yml` — tech / QA / security; [Щоденні аудити](daily-audits.md). |
| **Self-heal (діагностика)** | `workflow-self-heal.yml` — **не** те саме, що SDLC intake; див. каталог workflow. |
| **Hosted E2E** | Playwright у `website/` + `e2e-regression-dev.yml` — URL у [Pre-release та E2E](pre-release-e2e.md). |
| **Промоут / реліз** | Bunny promote workflows + ручні ворота — там само. |

**Правило:** якщо в **твоєму** форку бракує рядка з цієї таблиці — ти ще не переніс Ship, а лише встановив чат-бота.

---

## Порядок читання для імплементаторів

1. [SDLC (розклад)](sdlc-scheduled.md) — зрозумій **сітку**, перш ніж чіпати промпти.  
2. [Налаштування оператора](operator-setup.md) — секрети, дзеркало env, локальний `cli`.  
3. [Каталог workflow](workflows-catalog.md) — який YAML на яке питання відповідає.  
4. [Щоденні аудити](daily-audits.md) — **після** того, як доставка стала нудною.  
5. [Pre-release та E2E](pre-release-e2e.md) — коли готові до hosted regression і дисципліни промоуту.  
6. [Cursor Automations](cursor-automations.md) — лише якщо порівнюєш або мігруєш з продукту Cursor Automations.

---

## Предметний покажчик

| Тема | Сторінка |
|------|----------|
| SDLC cron, pick, колонки | [SDLC (розклад)](sdlc-scheduled.md) |
| Щоденні tech / QA / security аудити | [Щоденні аудити](daily-audits.md) |
| Dev/prod URL, E2E, промоут | [Pre-release та E2E](pre-release-e2e.md) |
| Секрети, змінні, локальний дебаг | [Налаштування оператора](operator-setup.md) |
| Таблиця YAML → призначення | [Каталог workflow](workflows-catalog.md) |
| Cursor Automations vs наш дефолт | [Cursor Automations](cursor-automations.md) |

---

## Репозиторій

[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)

---

## Чесно про scope

Назви на кшталт **ElMundi pre-release**, Bunny, dev.elmundi.com — вибір **цієї** орг. У форку перейменуй проєкти, URL і секрети; **Ship** (вкладка **Framework**) лишається в силі.

Мануал публікується на **https://ship.elmundi.com** після деплою доків; доки ні — збирай локально: [PDF та офлайн](../../pdf-export.md).
