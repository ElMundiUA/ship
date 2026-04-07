# SDLC — шість колонок + розклад GitHub

**Призначення:** експлуатація **розкладеної смуги SDLC** (intake → developer) з Linear + GitHub Actions + Cursor Cloud Agent.  
**Аудиторія:** інженери та платформені оператори.  
**Результат:** розуміння колонок, cron, секретів, ручних запусків і гігієни черг.

## Огляд

Linear — **джерело істини** для стану issue.

Етапи **intake → dev** виконує **`.github/workflows/linear-agent-sdlc-scheduled.yml`**. У цьому деплої Cursor Automations для цих етапів **вимкнені**. GitHub **планує** або **`workflow_dispatch`**; робота ролей — у **Cursor Cloud Agent** через `cloud-agent-launch.mjs`.

**Self-heal:** **`workflow-self-heal.yml`** — окремий каденс (спочатку CLI, опційний агент). Це **не** ті самі job, що intake/BA/developer.

!!! tip "Сайт документації Ship"
    **Прод:** [ship.elmundi.com](https://ship.elmundi.com/). **Локально:** з `tools/linear-agent`, `pip install -r requirements-docs.txt` → `mkdocs serve`. Почни з [Головна](../../index.md).

## Розклад (канон)

**UTC, парні години:**

| Хвилина | Роль |
|---------|------|
| :10 | intake |
| :25 | clarification |
| :40 | BA |
| :55 | developer |

**Непарні години :15:** `workflow-self-heal.yml` ([Каталог workflow](workflows-catalog.md)).

Не дублюй цю сітку в інших документах — **посилайся сюди**.

## Необхідні секрети GitHub

Репозиторій **ElMundiUA/elmundi** має мати **repository** secrets:

| Secret | Призначення |
|--------|-------------|
| `LINEAR_API_KEY` | Pick-скрипти, `dist/cli.js start`, коментарі агента |
| `CURSOR_API_KEY` | `cloud-agent-launch.mjs` |

Без `LINEAR_API_KEY` крок **Pick issue** падає з `MISSING_LINEAR_API_KEY`.

**Важливо:** кожен cron — **окремий** run з **однією** job. Зелений run **не** означає, що відпрацював developer: лише **SDLC Developer** (:55) переносить **Todo → In Progress**.

## Обсяг: проєкт, Backlog, Todo

Усі pick-скрипти SDLC фільтрують за проєктом **ElMundi pre-release** (перевизначення: **`LINEAR_SDLC_PROJECT_ID`** / **`LINEAR_SDLC_PROJECT_NAME`**).

**Backlog** — лише люди. Щоб запустити ланцюжок, перенеси картку в **Todo**.

**Developer pick:** **Todo** + **`ready:developer`** + той самий проєкт.

## Колонки (шість)

| # | Статус | Значення |
|---|--------|----------|
| 1 | Backlog | Лише тріаж; без SDLC pick |
| 2 | Todo | Intake → … → (з `ready:developer`) developer |
| 3 | In Progress | Реалізація |
| 4 | In Review | PR, preview, QA |
| 5 | Done | Готово |
| 6 | Blocked | Стоп |

## GitHub: pick → Cloud Agent

| Job | Pick-скрипт | Cloud prompt |
|-----|-------------|--------------|
| intake | `scripts/pick-intake-issue.mjs` | `cloud-prompts/intake.md` |
| clarification | `scripts/pick-clarification-issue.mjs` | `cloud-prompts/clarification.md` |
| ba | `scripts/pick-ba-issue.mjs` | `cloud-prompts/ba.md` |
| developer | `scripts/pick-next-dev-issue.mjs` | `cloud-prompts/developer.md` |

**Concurrency:** `concurrency.group` на етап, **`cancel-in-progress: false`**.

**Linear з агента:** [Секрети Cursor Cloud](../../tools/cursor-cloud-agent.md).

## Ручний запуск

```bash
gh workflow run linear-agent-sdlc-scheduled.yml -f role=developer -f issue=ELM-XX
```

## Чеклист упродовж дня

1. Ранок: Actions → **Linear SDLC (scheduled)** — зелені run; якщо червоні — логи + `auto:failed`.
2. Черги: `node scripts/agent-queue-snapshot.mjs`
3. Throughput: щонайбільше одна задача на слот ~2 год на роль.
4. Наздогнати: `workflow_dispatch` з `issue` і `role`.
5. Вечір: snapshot + дошка.

**Тикет лишається Todo після зеленого run:** [Усунення несправностей](../../framework/index.md#when-things-break).

## Опційні розширення

- `linear-agent-webhooks.yml` — A5/A6/A7.
- Після `start` developer у In Progress — не потрапляє в Todo pick.

## Мітки

```bash
cd tools/linear-agent && node scripts/sync-linear-team-labels.mjs
```

## Pre-release, E2E, прод

**[Pre-release та E2E](pre-release-e2e.md)** — канонічний гайд.

**Пов’язано:** [Глосарій](../../framework/index.md#vocabulary) · [Каталог workflow](workflows-catalog.md) · [Усунення несправностей](../../framework/index.md#when-things-break).
