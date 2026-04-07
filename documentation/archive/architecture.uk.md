# Архітектура та потоки даних

Виконання прив’язане до **GitHub Actions** у **репозиторії, де запускаються workflow** (лише цей пакет Ship або монорепо продукту). **Cursor Cloud Agent** працює в хмарі Cursor проти клонованого репо; **Linear** отримує оновлення через API, коли є ключі.

!!! note "Діаграми на цій сторінці"
    **Контекст системи** — компоненти та межі довіри (`architecture.svg`). **Стани SDLC** — Backlog vs вхід у Todo (`sdlc-states.svg`, також на [Бачення та масштабування](enterprise.md)). Джерела: `docs/diagrams/*.d2`; SVG перегенеровуються під `mkdocs build`, якщо `d2` у `PATH`.

## Контекст системи (D2)

Згенеровано з `docs/diagrams/architecture.d2` (перегенеровується під час збірки, якщо встановлено CLI `d2`).

![Контекст системи — Linear, GitHub Actions, Cursor, людські ворота](diagrams/architecture.svg)

_Джерело:_ редагуй `diagrams/architecture.d2`, потім `d2 diagrams/architecture.d2 diagrams/architecture.svg` або покладися на хук MkDocs `hooks/d2_prebuild.py`.

## Акцент на станах SDLC (D2)

Високорівневий вигляд відмінності **Backlog** (людина) проти **Todo** (вхід автоматизації).

![Станова машина SDLC (спрощено)](diagrams/sdlc-states.svg)

## Проєкти Linear

Типовий поділ (назви **ваші** — через env; один конкретний приклад: [Examples → Reference org](../examples/elmundi/index.md)):

| Проєкт | Роль |
|--------|------|
| **Delivery / pre-release** | Операційний SDLC. Автоматизація **не** обирає з **Backlog**; issues мають бути в **Todo** з pick-фільтрами (проєкт + мітки). |
| **Tech debt** | Висновки tech architect і QA architect (на основі доказів). |
| **Security** | Залежності/безпека зі сканерів (дедупліковано). |

Створи tech/security проєкти один раз: `node scripts/ensure-audit-linear-projects.mjs` (див. [Щоденні аудити](DAILY-AUDIT-ROLES.md)).

## Межі відповідальності

- **GitHub** — тригери, секрети, sparse checkout, `node scripts/…`.
- **Pick-скрипти** — детермінований вибір; бізнес-правила живуть у Linear + промптах.
- **Cursor Cloud Agent** — зміни коду, за потреби виклики Linear API, PR за контрактом промпта.
- **Люди** — тріаж Backlog, merge, продакшн-промоут, фінальні стани.

Далі: операційні деталі в [SDLC (розклад)](SDLC-AUTOMATION-SETUP.md).
