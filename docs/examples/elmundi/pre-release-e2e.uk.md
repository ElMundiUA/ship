# Pre-release: dev / prod, деплой, E2E, Linear

Єдиний довідник для **go/no-go**, узгодження автоматизації та SDLC (GitHub Actions + Linear).

**На цій сторінці:** [Домени та гілки](#domains--branches) · [Відповідні GitHub Actions](#relevant-github-actions) · [Bunny / ключі](#bunny-keys-operators) · [Дублікати PR](#duplicate-prs-for-one-elm-ticket) · [Ручний smoke](#manual-smoke-gono-go) · [Автоматизований regression](#automated-regression-on-dev) · [Проєкт Linear pre-release](#linear-project-elmundi-pre-release) · [Цілісність SDLC](#sdlc-integrity-after-changes)

!!! note "Експорт для стейкхолдерів"
    Тут є **специфічні для деплою** hostname та інфраструктура. Для **клієнтського PDF** узагальни URL і секції хостингу або познач документ як **лише internal**.

## Домени та гілки

<a id="domains--branches"></a>

| Середовище | URL | Джерело коду |
|------------|-----|--------------|
| **Dev (hosted)** | https://dev.elmundi.com | `main` → `docker-build-push.yml` → Bunny **elm-dev** (Magic Containers) |
| **Prod** | https://www.elmundi.com | Ручний промоут образу з Docker Hub → `bunny-promote-prod-*.yml` |

**Окремої** довгоживучої git-гілки **`dev` немає**: **dev = останній успішний деплой з `main`**.

## Відповідні GitHub Actions

<a id="relevant-github-actions"></a>

| Workflow | Призначення |
|----------|-------------|
| `Build and Push Docker Images` | Push у `main` → тег `v0.0.x`, образ `dekus/elmundi-frontend:<version>`, оновлення **elm-dev** |
| `Promote latest release to Bunny (prod)` | `workflow_dispatch` → prod на **останній** git-тег `v*` |
| `Promote specific tag to Bunny (prod)` | `workflow_dispatch` → prod на обраний тег |
| **`E2E regression (dev.elmundi.com)`** | Розклад + ручний повний Playwright **regression** проти **живого dev** |
| `PR Checks + Preview Deploy` | PR → smoke + preview |
| `Linear SDLC (scheduled)` | intake / clarification / BA / developer (Cloud Agent); pick **Todo** + **ElMundi pre-release** (`LINEAR_SDLC_PROJECT_*`) |

## Bunny / ключі (оператори)

<a id="bunny-keys-operators"></a>

- **Magic Containers API:** exchange через `https://api.bunny.net/apikey/exchange` з **`BUNNY_MAIN_API_KEY`** (може відрізнятися від `BUNNYNET_API_KEY`, який може давати 401 на exchange).
- **Dev app** у MC: hostname **dev.elmundi.com** (id з API — не плутати з prod).
- **Prod:** **www.elmundi.com**; у `bin/.env` **`BUNNY_APP_ID`** часто вказує на **prod** — для CI/dev використовуй GitHub Environment **elm-dev**.

## Дублікати PR для одного ELM-тикета

<a id="duplicate-prs-for-one-elm-ticket"></a>

Якщо існують дві гілки (`feature/ELM-XX-auto` і `fix/ELM-XX-auto`): developer Cloud Agent націлений на **`fix/ELM-XX-auto`** (`cloud-agent-launch.mjs`). Закрий зайві PR **без merge** (залиш правильний).

## Ручний smoke go/no-go

<a id="manual-smoke-gono-go"></a>

Чеклист: **`website/docs/manual-smoke-dev-to-prod.md`** (Mailosaur, Paddle sandbox на dev, блокери).

## Автоматизований regression на dev

<a id="automated-regression-on-dev"></a>

- **CI:** `.github/workflows/e2e-regression-dev.yml` — smoke + `tests/e2e/regression`, `PLAYWRIGHT_BASE_URL=https://dev.elmundi.com`.
- **Локально:** `cd website && npm run test:e2e:regression:dev` + змінні з `website/.env` (`env.example.txt`).

### Секрети / vars (повний CI run)

**Secrets and variables → Actions:**

- `MAILOSAUR_API_KEY`, `MAILOSAUR_SERVER_ID`
- Опційно: `E2E_SUBSCRIBER_EMAIL`, `E2E_FREE_EMAIL`
- Опційно: `PADDLE_CLIENT_TOKEN`, `PADDLE_PRICE_ID_MONTHLY` (job може встановити `PADDLE_ENVIRONMENT=sandbox`)

Без Mailosaur більшість OTP-тестів **skip** — workflow все одно дає частковий сигнал.

## Проєкт Linear **ElMundi pre-release**

<a id="linear-project-elmundi-pre-release"></a>

Баги з E2E на dev / pre-release. Скрипт (може створити дублікати при сліпому повторному запуску):

```bash
cd tools/linear-agent && node scripts/create-prerelease-e2e-bugs.mjs
```

Потрібен `LINEAR_API_KEY` у `tools/linear-agent/.env`.

## Цілісність SDLC (після змін)

<a id="sdlc-integrity-after-changes"></a>

1. [SDLC (розклад)](sdlc-scheduled.md) — сітка cron, секрети.
2. [Каталог workflow](workflows-catalog.md) — повний список workflow.
3. `cloud-prompts/developer.md` + `_base.md` — гілка `fix/ELM-XX-auto`, анти-дублікат PR.
4. Локально: `bash tools/linear-agent/scripts/verify-setup.sh`.
5. GitHub: останні **E2E regression (dev)** і **Linear SDLC (scheduled)** зелені або задокументовані збої.
