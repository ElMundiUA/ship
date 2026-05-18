# Ship E2E (Playwright)

End-to-end checks for the **operator console** and a **sandbox GitHub repo** after Ship onboarding.

## Задеплоенный dev (основной таргет)

1. Скопируй `e2e/.env.deployed.example` → `e2e/.env` и заполни URL **консоли** и **`E2E_SHIP_API_BASE`** (тот же origin, что в `SHIP_API_URL` у консоли).
2. Один раз сохрани `storageState` после логина Auth0 и пропиши `E2E_STORAGE_STATE` (для wired + journey).
3. В Console сделай **mint CLI token** с правами workspace **admin** → `E2E_SHIP_API_TOKEN` (для seed через REST и `ship-api.*`).
4. Полный прогон с метками `@deployed`:

```bash
cd e2e && npm run test:deployed
```

**CI в монорепо:** воркфлоу Playwright в `.github/workflows/` нет — прогон только локально/вручную по шагам выше (переменные те же: `E2E_CONSOLE_BASE_URL`, `E2E_SHIP_*`, storage, sandbox, Mailosaur и т.д.).

## Implementation plan (phases)

| Phase | What | How |
| ----- | ---- | --- |
| **A — Smoke (CI default)** | Login/marketing shell loads | `*.public.spec.ts`, no secrets |
| **B — Onboarding UI** | Authenticated user sees wizard steps or “You’re wired in” | Save Auth0 session → `E2E_STORAGE_STATE`, `*.wired.spec.ts` |
| **B2 — GitHub App install** | Кнопка в консоли → мастер на `github.com` → редирект обратно в онбординг | `E2E_RUN_GITHUB_APP_INSTALL=1` + тот же `E2E_STORAGE_STATE`; в storage желательно добавить cookies **github.com** (логин в том же браузере до «Save storage»). Пароль: `E2E_GITHUB_USERNAME` / `E2E_GITHUB_PASSWORD` только для бота **без 2FA**. Опционально: `E2E_GITHUB_INSTALL_ACCOUNT`, `E2E_GITHUB_REPO_FULL_NAME` |
| **C — Sandbox repo** | `.ship/config.yml` + workflows exist on test repo | `E2E_SANDBOX_REPO` + `GITHUB_TOKEN`, `*.sandbox.spec.ts` |
| **D — Console surfaces** (после онбординга) | Дашборд → пайплайны → clarifications → improvements → feedback → navigator → каталог → знания → metrics → settings → integrations → members → audit + клики по левому меню | `tests/console-flows.wired.spec.ts`, serial, нужен `E2E_STORAGE_STATE` |
| **D2 — Process editor** | Deep link `/process/development`, Flow/Capacity tabs, stage inspector + review summary, Capacity schedule (`?tab=schedule`), repo selector (`?repo=`), locked prerequisite banner | `tests/process-editor.wired.spec.ts`, serial; `E2E_STORAGE_STATE` + `E2E_SHIP_API_BASE` / `E2E_SHIP_API_TOKEN` for fixture probe; optional `E2E_SANDBOX_REPO`, `E2E_PROCESS_EDITOR_LOCKED_WORKSPACE_ID` |
| **D3 — Knowledge topic view** | `/knowledge/topics/{topic_tag}` SSR detail: article + claims, empty state for unknown tag, click-through from `/knowledge` | `tests/knowledge-topic-view.wired.spec.ts`; `E2E_STORAGE_STATE` + `E2E_SHIP_API_*`; optional `E2E_KNOWLEDGE_TOPIC_TAG` (overrides `GET …/topic-views?limit=1` probe); happy path skips when workspace has no rendered views |
| **E — Дашборд + API оркестрации** | UI: «Recommended» + недавние прогоны (`dashboard-delivery.wired.spec.ts`). API: `/v1/workspaces`, pipelines, dashboard, clarifications, improvements, artifact-feedback (`ship-api.sandbox.spec.ts` + `E2E_SHIP_API_*`). GitHub: список workflow runs + опционально label `ship:needs-clarification` (`github-actions.sandbox.spec.ts`) |
| **F — Сквозные journey** | Clarification: POST → ответ формой в UI (`journey-clarification.wired.spec.ts`). Improvement: POST → Accept (`journey-improvement.wired.spec.ts`). Health dev: `/login` + `GET /v1/health` (`deployed-health.public.spec.ts`). Без сессии: редирект на логин (`session.noauth.spec.ts`, проект `noauth`) |
| **G — Трекер GitHub → Ship** | Issue + лейбл + коммент `@ship clarification:` → `POST …/clarifications/sync` → poll GET → опционально UI (`tracker-github-clarification.wired.spec.ts`). Нужны репа с Ship App, трекер GitHub Issues в воркспейсе, PAT с `issues:write` |
| **H — Full journey + reset** | Сквозной Elmundi-подобный путь: GitHub App (опц.) → preset + sandbox-репо → трекер GitHub Issues → done, с проверкой seed pipelines через Ship API (`full-journey.wired.spec.ts`). Откат состояния для повторного прогона: `full-journey-reset.sandbox.spec.ts` |
| **I — Live staging automation** | Scheduled/manual staging suite: aggregate product/API check (`live-full-journey.wired.spec.ts`), Mailosaur invite delivery (`live-mailosaur-invite.wired.spec.ts`), external provider probes (`live-integrations.sandbox.spec.ts`) |
| **J — Navigator memory (E17)** | Isolated `e2e-navigator` workspace + 2 service users + 2 PATs. Contract API (`navigator-memory.wired.spec.ts`), cross-user isolation (`navigator-tenancy.wired.spec.ts`), Console UI (`navigator-memory-ui.wired.spec.ts`), LLM-burning SSE ring gated behind `E2E_RUN_NAVIGATOR_STREAM=1` (`navigator-memory-stream.wired.spec.ts`). Provisioning: `python tools/scripts/setup_e2e_navigator_workspace.py`. Required env: `E2E_NAVIGATOR_WORKSPACE_ID`, `E2E_NAVIGATOR_PAT_PRIMARY`, `E2E_NAVIGATOR_PAT_SECONDARY`. Detail in **Navigator memory (E17)** section below |

**Регистрация в Ship не автоматизируется** — нужна уже сохранённая сессия. **Установка GitHub App** — см. фазу B2 (`tests/github-app.wired.spec.ts`). Linear/Jira/Notion/Slack/etc. покрываются отдельными live probe specs, чтобы OAuth/provider flakiness не ломала основной onboarding path.

## Запись демо-видео полного journey

`full-journey.wired.spec.ts` оптимизирован под CI: если backend уже считает workspace полностью wired, wizard перепрыгивает шаги и тест проходит за ~4 секунды без визуальной части — для регрессии норм, для записи бесполезно.

Для записи есть отдельный сценарий `tests/demo-full-journey.wired.spec.ts` (тег `@demo`):

- сначала ресетит sandbox (закрывает Ship-PR'ы/issues, отвязывает sandbox-репо, чистит tracker integrations);
- идёт по каждому шагу через `?step=…` пины (URL-пин выигрывает над auto-resume);
- на каждом экране кликает реальные кнопки → backend живой;
- если deployed console ещё на 3-step flow (без knowledge), step 4 проскакивается с аннотацией.

Записать (~25 сек видео):

```bash
cd e2e
E2E_RUN_DEMO_JOURNEY=1 \
E2E_RUN_KNOWLEDGE_SEED=1 \
npx playwright test demo-full-journey.wired.spec.ts \
  --config=playwright.demo.config.ts
```

`playwright.demo.config.ts` включает `video: "on"`, trace, скриншоты, slowMo (350ms по умолчанию, override через `E2E_DEMO_SLOWMO`), 1440×900 viewport.

Видео ляжет в `test-results/<name>/video.webm`. Конвертация в шарабельный mp4:

```bash
ffmpeg -y -i test-results/<name>/video.webm \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart -preset slow -crf 22 \
  demo-recordings/full-journey.mp4
```

Чтобы записать **полный 4-step flow** (с knowledge step, который пока только локально), запусти console локально (`cd console && npm run dev`), сохрани свежий `storageState` против `http://127.0.0.1:3001`, и пропиши `E2E_CONSOLE_BASE_URL=http://127.0.0.1:3001` + `E2E_SHIP_API_BASE` на тот backend, который видит локальную консоль.

### Single-take product tour

`tests/product-tour.wired.spec.ts` — один тест, который за ~95 секунд проходит весь продукт:

```
wizard (опц.) → dashboard → pipelines → pipeline run → clarifications →
improvements → feedback → navigator → knowledge → catalog →
repo secrets → metrics → settings → members → integrations →
audit → dashboard
```

Между экранами пауза `E2E_TOUR_DWELL_MS` (по умолчанию 2500ms). Поэтому Playwright записывает один непрерывный `video.webm` (~2 МБ) без склейки. Запуск:

```bash
cd e2e
E2E_RUN_PRODUCT_TOUR=1 \
E2E_TOUR_INCLUDE_WIZARD=1 \
E2E_RUN_KNOWLEDGE_SEED=1 \
npx playwright test product-tour.wired.spec.ts \
  --config=playwright.demo.config.ts
```

Без `E2E_TOUR_INCLUDE_WIZARD=1` тур начинается прямо с дашборда (быстрее, не трогает sandbox). С `E2E_RUN_KNOWLEDGE_SEED=1` wizard реально откроет seed-PR в sandbox-репе.

После прогона:

```bash
ffmpeg -y -i $(find test-results -name 'video.webm' | head -1) \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart -preset slow -crf 22 \
  demo-recordings/product-tour.mp4
```

## Run locally

```bash
# from repo root — install once
npm install

# Terminal A: console + backend (see console README / docker-compose)
cd console && npm run dev

# Terminal B
cd e2e
export E2E_CONSOLE_BASE_URL=http://127.0.0.1:3001
npm test
```

## Authenticated session (phase B)

```bash
cd e2e
export E2E_CONSOLE_BASE_URL=https://your-console-host
npx playwright codegen "$E2E_CONSOLE_BASE_URL/login"
# Sign in; then in codegen: Storage → Save as e2e/.auth/user.json
export E2E_STORAGE_STATE=e2e/.auth/user.json
npm test
```

### GitHub App (phase B2)

1. В том же окне `codegen` после входа в Ship откройте `https://github.com/login` и залогиньтесь тестовым пользователем — затем сохраните storage (cookies для **обоих** origin).
2. Или передайте логин/пароль только для GitHub: `E2E_GITHUB_USERNAME`, `E2E_GITHUB_PASSWORD` (аккаунт без 2FA).
3. Запуск только этого сценария:

```bash
export E2E_RUN_GITHUB_APP_INSTALL=1
export E2E_GITHUB_REPO_FULL_NAME=your-org/sandbox-repo   # optional
export E2E_GITHUB_INSTALL_ACCOUNT=YourOrg             # optional, account picker
npx playwright test --project=authenticated tests/github-app.wired.spec.ts
```

## Ship HTTP API (phase E)

После mint токена в консоли:

```bash
export E2E_SHIP_API_BASE=https://your-backend-origin
export E2E_SHIP_API_TOKEN=ship_…
# optional
export E2E_WORKSPACE_ID=…

npx playwright test --project=sandbox-api e2e/tests/ship-api.sandbox.spec.ts
```

## GitHub Actions + label (phase E)

```bash
export E2E_SANDBOX_REPO=owner/sandbox-repo
export GITHUB_TOKEN=ghp_…
# optional: fail if label missing
export E2E_EXPECT_SHIP_CLARIFICATION_LABEL=1

npx playwright test e2e/tests/github-actions.sandbox.spec.ts
```

## Dashboard strips (phase E, UI)

Требует такой же `E2E_STORAGE_STATE`, что и остальные `*.wired.spec.ts`:

```bash
npx playwright test e2e/tests/dashboard-delivery.wired.spec.ts
```

## Process editor (phase D2)

Wired coverage for `/process/development` — Flow/Capacity tabs, stage inspector edits, schedule panel, repo selector, and optional locked-banner workspace.

```bash
export E2E_STORAGE_STATE=e2e/.auth/user.json
export E2E_SHIP_API_BASE=https://api.dev.example.com
export E2E_SHIP_API_TOKEN=ship_...
# optional: pick sandbox repo when several are activated
# export E2E_SANDBOX_REPO=your-org/e2e-sandbox
# optional: workspace UUID missing tracker/orchestrator/default agent (locked banner)
# export E2E_PROCESS_EDITOR_LOCKED_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

npx playwright test --project=authenticated tests/process-editor.wired.spec.ts
```

## Sandbox repo checks (phase C)

After onboarding completed on `owner/sandbox-repo`:

```bash
export E2E_SANDBOX_REPO=owner/sandbox-repo
export GITHUB_TOKEN=ghp_...   # fine-grained: Contents read on that repo
npm test
```

## Full journey (phase H)

Сквозной сценарий «как в Эльмунди»: один тест проводит аккаунт через мастер, активирует preset + тестовый репозиторий, подключает трекер GitHub Issues и проверяет, что Ship API увидел правильный набор pipelines.

```bash
export E2E_RUN_FULL_JOURNEY=1
export E2E_STORAGE_STATE=e2e/.auth/user.json
export E2E_SANDBOX_REPO=your-org/e2e-sandbox
export E2E_PRESET=web-app                      # web-app|api-backend|mobile-app|cli|monorepo|adoption-minimum
export E2E_SHIP_API_BASE=https://api.dev.example.com
export E2E_SHIP_API_TOKEN=ship_...             # workspace admin
# Если Ship App ещё не установлен на org, включи автоматизацию:
# export E2E_RUN_GITHUB_APP_INSTALL=1
# export E2E_GITHUB_INSTALL_ACCOUNT=YourOrg
# export E2E_GITHUB_REPO_FULL_NAME=$E2E_SANDBOX_REPO

npx playwright test --project=authenticated tests/full-journey.wired.spec.ts
```

Тест объявлен `serial` и `@deployed`; сверяет `enabled` pipelines с `PRESET_ENABLED_KINDS` из `apps/backend/app/services/default_pipelines.py`, а также что в `/v1/workspaces/{ws}/integrations` появился `kind=github`.

### Reset sandbox (повторный прогон)

Чтобы сделать новый полный прогон, верни окружение в исходное состояние. Запускается в проекте `sandbox-api` (без браузерной сессии):

```bash
export E2E_RESET_SANDBOX=1
export E2E_SANDBOX_REPO=your-org/e2e-sandbox
export GITHUB_TOKEN=ghp_...                    # репо-админ (issues+pulls+contents write)
export E2E_SHIP_API_BASE=https://api.dev.example.com
export E2E_SHIP_API_TOKEN=ship_...             # workspace admin
# Опционально удалить `ship/*`-ветки в тестовом репо:
# export E2E_RESET_DELETE_BRANCHES=1

npx playwright test --project=sandbox-api tests/full-journey-reset.sandbox.spec.ts
```

Что делает сброс:

- **GitHub**: закрывает issues с меткой `ship:needs-clarification` и/или заголовком `[e2e] …`, закрывает PR-ы, автором которых является Ship-бот или ветка начинается с `ship/` / `chore/ship-install-`, опционально удаляет такие ветки. История `main` **не переписывается**.
- **Ship**: `DELETE /v1/workspaces/{ws}/repos/{id}` для всех активированных репо + `DELETE /v1/workspaces/{ws}/integrations/{kind}` для `github|linear|notion` (404 — ок). Воркспейс остаётся, чтобы следующий прогон стартовал от известного tenant-id.

> Файлы в `.github/workflows/*.yml` и `.ship/config.yml` самого репо **не трогаются** — если ты установил Ship workflows через PR, либо смерж и потом откати отдельным reset-коммитом, либо держи тестовый репо на baseline-теге (`git push -f origin e2e-baseline:main` в отдельном ручном шаге).

## Live staging automation (phase I)

Scheduled CI runs the live staging suite against real services when the
corresponding secrets exist. Manual `workflow_dispatch` exposes the same knobs:

```bash
cd e2e
E2E_RUN_LIVE_FULL_JOURNEY=1 \
E2E_RUN_MAILOSAUR=1 \
E2E_RUN_EXTERNAL_INTEGRATIONS=1 \
npm run test:deployed
```

### Mailosaur invite delivery

`tests/live-mailosaur-invite.wired.spec.ts` creates a viewer invite through
`/v1/workspaces/{ws}/invites`, waits for the transactional email in Mailosaur,
extracts `/invite?token=...`, verifies `GET /v1/invites/{token}`, and opens the
public invite page.

Required:

- `MAILOSAUR_API_KEY`
- `MAILOSAUR_SERVER_ID`
- `E2E_RUN_MAILOSAUR=1`
- staging email provider must actually deliver to Mailosaur

Optional:

- `E2E_EMAIL_DOMAIN` — defaults to `<MAILOSAUR_SERVER_ID>.mailosaur.net`.
- `E2E_INVITEE_STORAGE_STATE` or CI secret `E2E_INVITEE_PLAYWRIGHT_STORAGE_JSON` — enables the final Accept click with a pre-authenticated invitee account whose email matches the generated Mailosaur address. Without it, the test still covers delivery, token peek, and invite page rendering.

### External integration probes

`tests/live-integrations.sandbox.spec.ts` probes each configured provider
independently. Missing secrets skip only that provider.

Workspace integration secrets:

- GitHub: `E2E_GITHUB_TOKEN` or `GITHUB_TOKEN`
- Linear: `E2E_LINEAR_API_KEY`
- Jira: `E2E_JIRA_SITE`, `E2E_JIRA_EMAIL`, `E2E_JIRA_API_TOKEN`
- Notion: `E2E_NOTION_TOKEN`
- Slack: `E2E_SLACK_BOT_TOKEN`
- GitLab: `E2E_GITLAB_TOKEN`, optional `E2E_GITLAB_HOST`, `E2E_GITLAB_GROUP`
- Webhook: `E2E_WEBHOOK_URL`, `E2E_WEBHOOK_SECRET`
- Teams: `E2E_TEAMS_WEBHOOK_URL`
- OTEL: `E2E_OTEL_ENDPOINT`, `E2E_OTEL_BEARER_TOKEN`
- S3 export: `E2E_S3_BUCKET`, `E2E_S3_ACCESS_KEY_ID`, `E2E_S3_SECRET_ACCESS_KEY`, optional `E2E_S3_REGION`

Native integration secrets:

- Atlassian/Jira: `E2E_JIRA_SITE`, `E2E_JIRA_EMAIL`, `E2E_JIRA_API_TOKEN`, optional `E2E_JIRA_PROJECT`
- GitLab native: `E2E_GITLAB_TOKEN`, optional `E2E_GITLAB_HOST`, `E2E_GITLAB_GROUP`
- Azure DevOps: `E2E_AZURE_DEVOPS_ORG`, `E2E_AZURE_DEVOPS_PAT`, optional `E2E_AZURE_DEVOPS_PROJECT`

### Extended reset

`E2E_RESET_EXTERNAL_INTEGRATIONS=1` extends `full-journey-reset.sandbox.spec.ts`
so it removes all workspace-level integration probe rows and disables native
installations. Keep this flag for dedicated e2e workspaces only.

## Navigator memory (E17)

Покрытие memory-фичи (`mem0`-бэкенд, Console `/memory`-страница, REST `/v1/workspaces/{ws}/navigator-memories`) делится на 4 спека, разнесённые по «кольцам стоимости».

| Spec | Purpose | Cost gate |
| ---- | ------- | --------- |
| `navigator-memory.wired.spec.ts` | Контракт REST (list / delete / forget / health / project-фильтр / `untagged`) | бесплатно — seed через `_test_seed` |
| `navigator-tenancy.wired.spec.ts` | Изоляция между пользователями в одной workspace (primary vs secondary PAT) | бесплатно |
| `navigator-memory-ui.wired.spec.ts` | Console `/memory` страница — render, row delete (arm/confirm), bulk-forget | бесплатно (но нужен `E2E_STORAGE_STATE`) |
| `navigator-memory-stream.wired.spec.ts` | SSE round-trip, per-message extraction, `recall`/`recall_context` тулы — **жжёт LLM-токены** | `E2E_RUN_NAVIGATOR_STREAM=1` |

### Подготовка

Один раз создай изолированный workspace + двух service-юзеров + два PAT'а:

```bash
PYTHONPATH=apps python tools/scripts/setup_e2e_navigator_workspace.py
```

Скрипт вытащит workspace UUID и оба `ship_pat_…` токена. Положи в `e2e/.env`:

```bash
E2E_NAVIGATOR_WORKSPACE_ID=<uuid>
E2E_NAVIGATOR_PAT_PRIMARY=ship_pat_…
E2E_NAVIGATOR_PAT_SECONDARY=ship_pat_…
```

Слаг этого workspace начинается с `e2e-` — это гейт для **sandbox seed**-эндпоинта `POST /v1/workspaces/{ws}/navigator-memories/_test_seed`, через который тесты пишут детерминированные факты в обход LLM-extract'a. На прод workspace'ах эндпоинт отвечает 404.

### Запуск

Все memory-спека делят один workspace и пишут в общую mem0/`navigator_memories`-таблицу, поэтому **обязательно `--workers=1`** — без этого два спека параллельно делают cleanup друг друга и видят чужие факты.

```bash
# Только контракт + tenancy + UI (бесплатно):
cd e2e
set -a && source .env && set +a
npx playwright test navigator-memory navigator-tenancy navigator-memory-ui --workers=1

# Полное покрытие, включая LLM-ring (платно):
E2E_RUN_NAVIGATOR_STREAM=1 npx playwright test navigator-memory --workers=1
```

### Запуск против localhost (E19 — без прод-зависимостей)

Для быстрого regression — целишь в локальный стек, поднятый `make dev-up`. Контракт + tenancy спеки идут за ~2с против Neon-free Postgres и Memory-адаптеров:

```bash
# 0) Поднять стек (один раз):
make dev-up

# 1) Засеять e2e-navigator workspace + 2 service users + 2 PATs
#    в локальный pg (одноразово; PATs печатаются только при создании):
DATABASE_URL=postgresql://ship:ship@localhost:5433/ship \
  E2E_SETUP_OPERATOR_EMAIL=dev@ship.dev \
  PYTHONPATH=apps .venv/bin/python tools/scripts/setup_e2e_navigator_workspace.py

# 2) Скопировать e2e/.env.local.example → e2e/.env.local и вписать
#    напечатанные UUID + PATs.

# 3) Прогнать:
cd e2e && set -a && source .env.local && set +a
npx playwright test navigator-memory.wired navigator-tenancy --workers=1
```

UI-спека (`navigator-memory-ui`) на локалке не работают из коробки — `E2E_STORAGE_STATE` рассчитан на Auth0-сессию, а local-auth-mode использует свою форму. Для UI-debug либо подними Auth0-mode локально, либо гоняй UI против прода.

### Чем покрыты milestones (M1–M19)

`navigator-memory.wired.spec.ts` — M1 (seed→list), M2 (list shape), M3 (delete), M4 (bulk-forget window), M5 (days clamp), M14 (project filter), M17 (delete audit), M18 (`untagged` filter), M19 (health counters). `navigator-tenancy.wired.spec.ts` — M10 (cross-user list/delete/health). `navigator-memory-ui.wired.spec.ts` — M11 (render), M12 (row delete UI), M13 (bulk-forget consent). `navigator-memory-stream.wired.spec.ts` — M6 (SSE), M7 (first-turn retrieval), M8 (`recall` tool), M9 (`recall_context`), M15 (extraction round-trip), M16 (audit log), M19 (live counters). M5b (30-min gap retrieval) покрыт юнитом в `apps/backend/tests/test_navigator_memory.py`.

## CI

В репозитории нет GitHub Actions workflow для e2e. Запускай локально: `cd e2e && npm test` / `npm run test:deployed` с `.env` по примерам выше. Секреты те же (`E2E_CONSOLE_BASE_URL`, `E2E_PLAYWRIGHT_STORAGE_JSON` или `E2E_STORAGE_STATE`, `E2E_SHIP_API_*`, sandbox, интеграции — см. фазы выше).
