# GitHub Actions в этом репозитории

Стартовые YAML для **установки в репозитории клиентов** живут в `backend/app/resources/starter_workflows/`. Сейчас seed bundle ставит ровно один воркфлоу — `ship-trigger-schedule.yml`; legacy lane wrappers и vendored `run-agent.workflow.yml` удалены вместе с командой `shipctl lanes`.

---

## С префиксом `Ship` в поле `name:` — оставлены

### 1. `docker-publish-platform.yml` — **Ship — platform images (backend + console + landing)**

**Когда:** push в `main`; PR по путям `backend/`**, `console/`**, `landing/**`, `Dockerfile`, `VERSION`, lockfile’ы и т.д.; `workflow_dispatch`.

**Что делает:** матрица из трёх сборок Docker (`ship-backend`, `ship-console`, `ship-docs`); на PR — verify-сборка, на `main` — push тегов и rollout в Bunny.

---

### 2. `ship-trigger-schedule.yml` — **Ship · Schedule trigger**

**Когда:** `*/30 * * * `* (каждые 30 минут) и `workflow_dispatch`.

**Что делает:** `shipctl trigger --event schedule --pipeline-fallback` возвращает один `next_action`: либо `routine` (cron-due) → `shipctl run --routine …`, либо `pipeline_pick` (когда рутины пусты) → `shipctl run --specialist …`. Один тик — одно действие.

---

## Без префикса `Ship` в `name:`

### 3. `version-check.yml`

**Когда:** PR (по путям версий) и каждый push в `main`.

**Что делает:** `node scripts/version.mjs check`.

---

### 4. `bundle-version-check.yml`

**Когда:** PR при изменениях сид-бандла / стартеров.

**Что делает:** `scripts/bundle-version-check.mjs` — бамп `BUNDLE_VERSION`.

---

### 5. `artifact-check.yml`

**Когда:** PR при изменениях `artifacts/`**.

**Что делает:** `scripts/ship_artifact_check.py` — `content_sha256` vs пересчёт.

---

### 6. `auto-tag-version.yml`

**Когда:** push в `main`, если изменился `VERSION`.

**Что делает:** тег `v`* если ещё нет.

---

### 7. `npm-publish-cli.yml` — **Publish @elmundi/ship-cli to npm**

**Когда:** теги `v`* / `cli-v*`, `workflow_dispatch`.

**Что делает:** публикация CLI на npm.

---

## E2E

Playwright в GitHub Actions не гоняем — см. `e2e/README.md`.

---

## Удалены из `.github/workflows/` (монорепо)


| Файл                                                                                   | Примечание         |
| -------------------------------------------------------------------------------------- | ------------------ |
| `lanes-smoke.yml`, стартеры (`ship-bootstrap`, `pr-and-ci-gate`, …), `e2e-console.yml` | см. историю репо   |
| `run-agent.yml`                                                                        | удалён вместе с `shipctl lanes` (Phase 11 cleanup). |


---

## Сводка


| Файл                          | Суть                  |
| ----------------------------- | --------------------- |
| `docker-publish-platform.yml` | Образы + деплой       |
| `ship-trigger-schedule.yml`   | Trigger каждые 30 мин |
| `version-check.yml`           | Версии синхрон        |
| `bundle-version-check.yml`    | Версия сид-бандла     |
| `artifact-check.yml`          | Хэши `artifacts/`**   |
| `auto-tag-version.yml`        | Автотег               |
| `npm-publish-cli.yml`         | npm publish CLI       |
