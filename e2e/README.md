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

**GitHub Actions:** `.github/workflows/e2e-console.yml` — секреты `E2E_CONSOLE_BASE_URL`, опционально `E2E_SHIP_API_BASE`, `E2E_SHIP_API_TOKEN`, `E2E_PLAYWRIGHT_STORAGE_JSON` (сырой JSON Playwright storage), `E2E_SANDBOX_REPO`, `E2E_GITHUB_TOKEN`.

## Implementation plan (phases)

| Phase | What | How |
| ----- | ---- | --- |
| **A — Smoke (CI default)** | Login/marketing shell loads | `*.public.spec.ts`, no secrets |
| **B — Onboarding UI** | Authenticated user sees wizard steps or “You’re wired in” | Save Auth0 session → `E2E_STORAGE_STATE`, `*.wired.spec.ts` |
| **B2 — GitHub App install** | Кнопка в консоли → мастер на `github.com` → редирект обратно в онбординг | `E2E_RUN_GITHUB_APP_INSTALL=1` + тот же `E2E_STORAGE_STATE`; в storage желательно добавить cookies **github.com** (логин в том же браузере до «Save storage»). Пароль: `E2E_GITHUB_USERNAME` / `E2E_GITHUB_PASSWORD` только для бота **без 2FA**. Опционально: `E2E_GITHUB_INSTALL_ACCOUNT`, `E2E_GITHUB_REPO_FULL_NAME` |
| **C — Sandbox repo** | `.ship/config.yml` + workflows exist on test repo | `E2E_SANDBOX_REPO` + `GITHUB_TOKEN`, `*.sandbox.spec.ts` |
| **D — Console surfaces** (после онбординга) | Дашборд → пайплайны → clarifications → improvements → feedback → navigator → каталог → знания → metrics → settings → integrations → members → audit + клики по левому меню | `tests/console-flows.wired.spec.ts`, serial, нужен `E2E_STORAGE_STATE` |
| **E — Дашборд + API оркестрации** | UI: «Recommended» + недавние прогоны (`dashboard-delivery.wired.spec.ts`). API: `/v1/workspaces`, pipelines, dashboard, clarifications, improvements, artifact-feedback (`ship-api.sandbox.spec.ts` + `E2E_SHIP_API_*`). GitHub: список workflow runs + опционально label `ship:needs-clarification` (`github-actions.sandbox.spec.ts`) |
| **F — Сквозные journey** | Clarification: POST → ответ формой в UI (`journey-clarification.wired.spec.ts`). Improvement: POST → Accept (`journey-improvement.wired.spec.ts`). Health dev: `/login` + `GET /v1/health` (`deployed-health.public.spec.ts`). Без сессии: редирект на логин (`session.noauth.spec.ts`, проект `noauth`) |
| **G — Трекер GitHub → Ship** | Issue + лейбл + коммент `@ship clarification:` → `POST …/clarifications/sync` → poll GET → опционально UI (`tracker-github-clarification.wired.spec.ts`). Нужны репа с Ship App, трекер GitHub Issues в воркспейсе, PAT с `issues:write` |

**Регистрация в Ship не автоматизируется** — нужна уже сохранённая сессия. **Установка GitHub App** — см. фазу B2 (`tests/github-app.wired.spec.ts`); OAuth других трекеров — пока вне scope.

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

## Sandbox repo checks (phase C)

After onboarding completed on `owner/sandbox-repo`:

```bash
export E2E_SANDBOX_REPO=owner/sandbox-repo
export GITHUB_TOKEN=ghp_...   # fine-grained: Contents read on that repo
npm test
```

## CI

Workflow `.github/workflows/e2e-console.yml` runs **public** tests against `E2E_CONSOLE_BASE_URL` (required). Add secrets for phased B/C when ready.
