# Секрети Cursor Cloud Agent (Linear з репо)

**GitHub → Settings → Secrets and variables → Actions:** репо потребує **`LINEAR_API_KEY`** і **`CURSOR_API_KEY`** (repository або org secrets). Без `LINEAR_API_KEY` падають кроки pick / `cli start`; без `CURSOR_API_KEY` Cloud Agent не стартує.

GitHub передає **`CURSOR_API_KEY`** при запуску агента. Щоб агент міг **оновлювати Linear** (intake / clarification / BA / developer), у Cursor також треба виставити **`LINEAR_API_KEY`** у середовищі Cloud Agent для цього репозиторію.

1. **Cursor Dashboard** → Cloud Agents / Repository / Environment (точна назва може відрізнятися).
2. Додай **`LINEAR_API_KEY`** (те саме значення, що й GitHub `LINEAR_API_KEY`). `GITHUB_TOKEN` для Linear зазвичай не потрібен.
3. Опційно: **`LINEAR_SDLC_PROJECT_ID`** або **`LINEAR_SDLC_PROJECT_NAME`** — SDLC pick обмежені одним проєктом Linear (дефолт: id ElMundi pre-release у коді).

Поки ключа немає в env агента, промпти можуть просити ручний коментар **`[LINEAR-DRAFT]`** на тикеті.

Довідник Cursor API: [Cloud Agents API](https://cursor.com/docs/background-agent/api/overview).
