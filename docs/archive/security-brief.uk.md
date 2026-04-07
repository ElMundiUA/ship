# Security brief (безпека та приватність)

**Призначення:** високорівневий наратив **потоків даних і довіри** для стейкхолдерів (не покрокове налаштування секретів).  
**Аудиторія:** security, архітектори, закупівлі.

## Компоненти

- **GitHub** — код, workflow, **Actions secrets** (`LINEAR_API_KEY`, `CURSOR_API_KEY` тощо).
- **Linear** — стани, коментарі, проєкти; оновлення через API за наявності ключів.
- **Cursor Cloud Agent** — у хмарі Cursor проти **клону** репо за викликом оркестратора.
- **Опційно:** Snyk, email, CDN — залежно від деплою.

## Секрети та дублювання

GitHub передає секрети в workflow. Щоб агент **оновлював Linear**, той самий credential може знадобитися в **середовищі Cloud Agent** для репо ([Секрети Cursor Cloud](CLOUD-AGENT-SECRETS.md)).

**Питання для закупівель:** які системи є **субпроцесорами** і чи прийнятне дублювання секретів за вашою політикою. Цей текст **не замінює** DPIA чи DPA.

## Потоки даних (концептуально)

1. Тригер workflow → checkout → метадані issue з Linear (API).
2. Запуск агента → промпт + контекст репо в Cursor API → гілка/PR.
3. Аудити → можуть використовувати **JSON Snyk** або аналіз репо; тикети лише з доказами ([Щоденні аудити](DAILY-AUDIT-ROLES.md)).

## Ризики (простою мовою)

- **Витік облікових даних** — найменші права, ротація, scope секретів.
- **Обробка третіми сторонами** — код і промпти можуть оброблятися постачальником агента; перегляньте умови Cursor, GitHub, Linear.
- **Prompt injection** — текст issue як недовірений вхід; пом’якшення в дизайні промптів і review.

## Зовнішні посилання

- [Cursor — Cloud Agents API](https://cursor.com/docs/background-agent/api/overview)
- [GitHub — Encrypted secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [Linear — API](https://developers.linear.app/)

## Деталі для операторів

[Секрети Cursor Cloud](CLOUD-AGENT-SECRETS.md) · [Автономний пайплайн](AUTONOMOUS-SETUP.md).
