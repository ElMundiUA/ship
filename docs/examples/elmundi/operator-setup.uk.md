# Налаштування автономного пайплайну

**Призначення:** передумови, **секрети та змінні GitHub**, локальний дебаг і режими створення PR для linear-agent.  
**Аудиторія:** платформені інженери, супровід.  
**Результат:** перевірене середовище, dry-run, зрозуміло, де **індекс YAML workflow**.

!!! tip "Розклад та індекс YAML"
    **Сітка cron SDLC** (парні години :10 / :25 / :40 / :55) і колонки: **[SDLC (розклад)](sdlc-scheduled.md)** (канон).  
    **Усі workflow-файли** в одній таблиці: **[Каталог workflow](workflows-catalog.md)**.  
    **Щоденні аудити:** **[Щоденні аудити](daily-audits.md)**.

## Передумови

1. **Linear** — команда з мітками: `stage:*`, `ready:*`, `flow:*`, `result:*`
2. **GitHub** — увімкнені Actions
3. **Cursor** — API key для Cloud Agent / CLI
4. **SendGrid** — листи ready-for-review (якщо використовуються)

## Секрети

GitHub **Settings → Secrets → Actions**:

| Secret | Опис |
|--------|------|
| `LINEAR_API_KEY` | Ключ Linear API |
| `GITHUB_TOKEN` | Автоматично; переконайся в `contents: write`, `pull-requests: write` |
| `CURSOR_API_KEY` | [Cursor dashboard](https://cursor.com/dashboard?tab=integrations) |
| `SENDGRID_API_KEY` | Транзакційні листи (якщо увімкнено у вашому деплої) |
| `SNYK_TOKEN` | Опційно: job безпеки в **`linear-agent-daily-audits.yml`**; без нього Snyk skip |

## Змінні (**Settings → Variables**)

| Variable | Опис |
|----------|------|
| `BUNNY_APP_ID` | Id app для preview-клонів у PR; без нього deploy у `pr-preview` може skip |
| `LINEAR_TEAM_KEY` | Опційно ключ команди Linear |
| `LINEAR_SELF_HEAL_ISSUE` | Issue для self-heal агента; без нього крок Launch skip |
| `LINEAR_TECH_DEBT_PROJECT_ID` | Опційно — [Щоденні аудити](daily-audits.md) |
| `LINEAR_SECURITY_PROJECT_ID` | Опційно; ціль для тикетів Snyk |
| `LINEAR_SDLC_PROJECT_ID` | Опційно; перевизначення проєкту SDLC pick |

**Агент + Linear env:** [Секрети Cursor Cloud](../../tools/cursor-cloud-agent.md).

## Швидкий чеклист дебагу

| Крок | Команда |
|------|---------|
| 1. Перевірка env | `bash scripts/verify-setup.sh` |
| 2. Перевірка тикета | `bash scripts/run-ticket-verify.sh ELM-XX` |
| 3. Dry-run | `bash scripts/run-autonomous-local.sh ELM-XX --verify-only` |
| 4. CI verify-only | `gh workflow run linear-agent-autonomous.yml -f verify_only=true` |

## Потік ініціалізації

```bash
cd tools/linear-agent
node dist/cli.js init --issue ELM-XX        # Feature
node dist/cli.js init --issue ELM-XX --bug  # Bug
```

Або автоматизація Linear для `ready:ba` / `ready:bug-agent` при створенні.

## Розклад автономного циклу

**`linear-agent-autonomous.yml`** має власний каденс (за замовчуванням кожні 6 год). **Доповнює** SDLC, не замінює.

```yaml
schedule:
  - cron: '0 */6 * * *'
```

## Хто створює PR

| Режим | PR |
|-------|-----|
| **`--cloud`** | Cloud Agent створює PR після завершення |
| **Локальний агент** | `run-autonomous-local.sh` → `pr-create` після push |
| **GitHub Actions** | SDLC або autonomous workflow → pick → Cloud Agent → PR |
| **`--no-agent`** | Вручну `node dist/cli.js pr-create -i ELM-XX` після push |

`pr-create` ідемпотентний.

## Тестування

### Локально

```bash
cd tools/linear-agent
bash scripts/run-autonomous-local.sh ELM-XX [--no-agent] [--cloud] [--yes] [--verify-only]
```

Потрібен `CURSOR_API_KEY` у `.env` для `--cloud`.

### GitHub Actions

1. `gh auth login`
2. `gh workflow run linear-agent-sdlc-scheduled.yml -f role=developer -f issue=ELM-XX`
3. `gh workflow run linear-agent-autonomous.yml -f issue=ELM-XX`

## Дебаг і верифікація

```bash
cd tools/linear-agent
bash scripts/verify-setup.sh [--issue ELM-XX] [--skip-bunny]
bash scripts/run-ticket-verify.sh ELM-XX [--release-check]
bash scripts/run-autonomous-local.sh ELM-XX --verify-only
node dist/cli.js next -r developer
gh workflow run linear-agent-autonomous.yml -f verify_only=true
```

**Якщо щось зламалося:** [Усунення несправностей](../../framework/index.md#when-things-break).
