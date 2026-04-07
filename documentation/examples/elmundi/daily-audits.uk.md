# Щоденні аудити — tech architect, QA architect, security

Три ролі запускаються **раз на добу** з **`linear-agent-daily-audits.yml`**, використовуючи той самий **Cursor Cloud Agent** entrypoint, що й SDLC (`scripts/cloud-agent-launch.mjs`). Вони **не** споживають чергу pre-release SDLC; аналізують репо (а для security — **JSON Snyk**) і **створюють** issues у Linear у виділених проєктах, коли це виправдано.

## Правило «без вигадування»

Промпти вимагають **перевірюваних доказів** (шляхи файлів, записи Snyk, наявні тести). Якщо нового немає — **без** тикетів Linear і **без** наповнювач-коментарів. Security: якщо Snyk показує **0** вразливостей, крок Cloud Agent **пропускається**.

## Проєкти Linear

| Проєкт | Хто пише |
|--------|----------|
| **ElMundi tech debt** | Tech architect, QA architect |
| **ElMundi security** | Security officer |

Створи один раз:

```bash
cd tools/linear-agent && node scripts/ensure-audit-linear-projects.mjs
# лише план: ... --dry-run
```

Опційні **variables** GitHub (без пошуку за іменем):

- `LINEAR_TECH_DEBT_PROJECT_ID`
- `LINEAR_SECURITY_PROJECT_ID`

Опційно: `LINEAR_TECH_DEBT_PROJECT_NAME`, `LINEAR_SECURITY_PROJECT_NAME`, якщо перейменував проєкти.

## Мітки

Після зміни списку міток:

```bash
cd tools/linear-agent && node scripts/sync-linear-team-labels.mjs
```

Дедуп: `source:tech-architect`, `source:qa-architect`, `source:security-officer`, `audit:auto`.

## Секрети та розклад

| Secret / var | Призначення |
|--------------|-------------|
| `LINEAR_API_KEY` | Резолв проєкту в `cloud-agent-launch` + оновлення агентом |
| `CURSOR_API_KEY` | Запуск Cloud Agent |
| `SNYK_TOKEN` | Лише security job (без токена — крок Snyk пропускається з рядком у логах) |
| `LINEAR_TEAM_KEY` | Як у SDLC, зазвичай `ELM` |

**UTC:** tech **05:20**, QA **05:50**, security **06:20** — підлаштуй cron у workflow.

## Ручний запуск

**Actions → Linear daily audits → Run workflow** — обери роль або `all`.

## Локальний дебаг

```bash
cd tools/linear-agent
export CURSOR_API_KEY=... LINEAR_API_KEY=... LINEAR_TEAM_KEY=ELM
node scripts/cloud-agent-launch.mjs --role=tech-architect --issue=NONE
# security зі звітом:
node scripts/cloud-agent-launch.mjs --role=security-officer --issue=NONE --report-file=/path/to/snyk.json
```

Промпти: `cloud-prompts/tech-architect.md`, `qa-architect.md`, `security-officer.md`.
