# Міграція на Cursor Automations

Поточний потік (GitHub Actions + linear-agent CLI + Cloud Agent API) можна перенести на **Cursor Automations** — нативні тригери та агенти всередині Cursor.

## Поточний стан (цей репозиторій)

| Область | Зараз |
|---------|--------|
| **Оркестратор** | GitHub Actions (`linear-agent-sdlc-scheduled.yml` та пов’язані — [Каталог workflow](workflows-catalog.md)) |
| **Запуск агента** | `cloud-agent-launch.mjs` + **Cursor Cloud Agents API** + `CURSOR_API_KEY` |
| **Метадані issue** | `dist/cli.js get` / Linear API через pick-скрипти та CLI |
| **Cursor Automations** | **Вимкнено** для етапів SDLC ([SDLC (розклад)](sdlc-scheduled.md)) |

## GitHub-оркестрація + агент vs Cursor Automations — чому міграція зазвичай **не потрібна**

У репозиторії навмисно лишають **GitHub Actions + детермінований pick + `cloud-agent-launch.mjs`**. Cursor Automations описані для повноти, **не** як рекомендований дефолт.

| Вимір | **Цей репо (GitHub + pick + Cloud Agent API)** | **Cursor Automations** |
|-------|-----------------------------------------------|-------------------------|
| **Коли стартує агент** | Лише якщо pick-скрипт **обрав issue**; запуски workflow за cron — переважно дешеві хвилини CI; **немає витрат на агента** на «порожніх» чергах. | Тригери (зміна статусу, розклад, webhook) можуть **запускати automation** навіть коли промпт одразу виходить; **облік cloud agent** може нараховуватися на «no-op» — **перевір у своєму плані Cursor**. |
| **Передбачуваність вартості** | Обмежено **слотами cron × ролі** і **≤1 issue на слот**; просто оцінити «максимум N викликів агента на добу». | Подієві тригери ростуть з активністю дошки; **сплески** важче обмежити без додаткових охорон. |
| **Упередженість (bias) і варіативність** | Той самий **версіонований** пакет `cloud-prompts/` + `.cursor/skills`; зміни через PR. | Промпти в **UI Automations**; легший **дрейф** між автоматизаціями; **bias LLM** є в обох стеках, але **governance** (review, тести, PR) сильніший, коли промпти в репо. |
| **Аудит для комплаєнсу** | Кроки прив’язані до **GitHub Actions run**, контексту коміту та тикета Linear — [Executive brief](../../framework/index.md#the-idea). | Логи Cursor + GitHub; картина **розкидана**, якщо Automations замінюють межу workflow. |
| **Детерміновані ворота** | Мітки + проєкт + колонка в **Node pick** до виклику агента. | Часто покладаються на **правила природною мовою в промпті** — простіше помилитися в конфігурації. |
| **Контроль потоку** | **Одна роль на вікно cron** — без штампеду ([Бачення та масштабування](../../framework/index.md#the-idea)). | Легше **накласти** запуски (кілька змін статусу, перетин розкладів), якщо немає зовнішніх блокувань. |
| **Гнучкість моделі / вендора** | Оркестрація — **HTTP + репо**; `cloud-agent-launch.mjs` — **єдина точка** для зміни параметрів API, endpoint або **рівня моделі за роллю** (коли провайдер це дає) — див. нижче. | Зазвичай **окрема конфігурація на automation**; зміна моделі = **багато тригерів**, не один скрипт. |

### Гнучкість моделей (чому поточний патерн краще масштабується по **ціні і якості**)

- **Різні моделі під різні ролі:** шлях запуску централізований. Як з’являються дешевші/швидші моделі для intake чи міток і «важчі» для developer-реалізації, можна **маршрутизувати за роллю** в одному місці, а не дублювати N automations.
- **Рух разом з ринком:** якщо з’явиться дешевший або open-weight стек за тим самим контрактом «checkout + гілка + PR» ([Бачення та масштабування](../../framework/index.md#the-idea)), міняєш **launch + секрети**, а не всі визначення тригерів у Linear.
- **Automations** часто **фіксують** один профіль агента на automation; щоб міксувати «дешево vs преміум» по етапах SDLC, потрібно **багато** окремих автоматизацій.

### Орієнтовні цифри вартості (не оферта — перевір [cursor.com](https://cursor.com) / dashboard)

Тарифи та включені ліміти **Cloud Agent** / **Automations** **часто змінюються**. Нижче — лише **порядок величин** для планування, **не** рахунок до сплати.

**Приклад припущень:** сітка SDLC = **4 ролі × ~12 парних годин на добу** ⇒ **~48 тиків планувальника на добу**. Якщо лише **25%** тиків реально роблять pick і запускають агента ⇒ **~12 запусків агента на добу**. Якщо Automations зав’язані на **ширші** події (кожен перехід у Ready без попереднього pick), для того самого беклогу легко отримати **десятки зайвих викликів на добу**.

| Умовний рядок | GitHub + pick (цей дизайн) | Дизайн на Automations |
|---------------|----------------------------|------------------------|
| **Планувальник / idle** | Переважно **хвилини GitHub Actions** при порожньому pick; **$0** маржинальної вартості агента, якщо issue не обрано. | Залежить, чи **оцінка тригера** тарифікується як використання агента — **уточни в Cursor**. |
| **Запусків агента / добу** (приклад) | ~**10–15** при низькому pick rate | Часто **вище** при «багатих» подіях, якщо немає дедуплікації. |
| **Чутливість на рік** | Якщо умовно **$1–5** за **реальний** запуск агента (ілюстрація), **12 × 250 робочих днів** ≈ **$3k–15k/рік** лише з SDLC — лінійно з частотою pick. | Додай **20–50%+**, якщо «холості» або дубль-тригери тарифікуються — **фактичні цифри індивідуальні**. |

**Дія:** вивантаж статистику використання з **Cursor** і зістав з часом **workflow runs** у GitHub **перед** рішенням про міграцію.

---

## Якщо все ж міграція — цільові моделі

Розділи **Варіант 1–4** нижче описують **цільові** архітектури.

**Контекст governance:** [Executive brief](../../framework/index.md#the-idea).

## Що дають Cursor Automations

- **Тригери:** Linear (створено issue, змінився статус), розклад (cron), webhook, GitHub
- **Інструменти:** відкрити PR, коментар у PR, Slack, MCP, memories
- **Білінг:** використання cloud agent (team plan)

## Варіант 1: Linear «Status changed» (типовий)

Коли issue переходить у **Ready** з `ready:developer`:

1. **cursor.com/automations** → New automation
2. **Trigger:** Linear → Status changed → New status: Ready
3. **Tools:** Open pull request, Linear (якщо є MCP)
4. **Repository:** ElMundiUA/elmundi, base: `main`
5. **Prompt** (ескіз):

```
You are the Developer agent for the Linear issue in this run.

RULES:
- Only proceed if the issue has label "ready:developer". Otherwise exit without changes.
- Update Linear: status In Progress, label "stage:developer".
- Implement per issue description.
- Run tests: cd website && npm run test && npm run test:e2e:smoke -- --project=chromium-desktop
- Branch: fix/{ISSUE_ID}-auto
- Commit: fix(ISSUE_ID): <short>
- Open PR with body containing: Closes {ISSUE_ID}
- If blocked: Linear comment "Blocked: <reason>" and exit.
```

**Обмеження:** «Status changed → Ready» спрацьовує для будь-якого переходу в Ready — охорона в промпті через `ready:developer`.

---

## Варіант 2: Webhook (мінімальні зміни в GitHub)

Залиш GitHub Actions оркестратором, але заміни Cloud Agent API на webhook Automation:

1. **cursor.com/automations** → New automation
2. **Trigger:** Webhook
3. **Tools:** Open pull request
4. **Prompt:** ті самі правила, що у варіанті 1; id issue приходить у тілі webhook

5. У **`linear-agent-sdlc-scheduled.yml`** (або окремому workflow) заміни `cloud-agent-launch` на:

```yaml
- name: Launch Cursor Automation via webhook
  run: |
    ISSUE="${{ steps.issue.outputs.issue }}"
    curl -X POST "${{ secrets.CURSOR_AUTOMATION_WEBHOOK_URL }}" \
      -H "Authorization: Bearer ${{ secrets.CURSOR_AUTOMATION_WEBHOOK_KEY }}" \
      -H "Content-Type: application/json" \
      -d "{\"issue\": \"$ISSUE\"}"
```

6. Збережи URL webhook + API key у Secrets GitHub.

---

## Варіант 3: Scheduled (повна заміна розкладу GitHub)

1. **Trigger:** Scheduled → напр. `0 */6 * * *`
2. **Repository:** ElMundiUA/elmundi
3. **Tools:** Open PR, Linear MCP (якщо налаштовано)

**Пробіл:** у Automation немає CLI `linear-agent` `next -r developer`. Потрібен **Linear MCP** (або еквівалент) для списку/фільтрації за мітками.

---

## Варіант 4: Linear «Issue created» + делегування

Використовуй правила тріажу Linear або ручне призначення на Cursor.

**Плюси:** без розкладу, нативно для Linear.  
**Мінуси:** призначення на issue або правила тріажу.

---

## Рекомендація

| Мета | Варіант |
|------|---------|
| Мінімум змін, зберегти розклад GitHub | **2 Webhook** |
| Нативний потік Linear | **1 Status changed** |
| Прибрати GitHub Actions повністю | **3** (потрібен Linear MCP) |

## Linear MCP (варіанти 1 і 3)

1. Cursor Dashboard → Integrations → Linear
2. Або додай Linear MCP server до проєкту
3. Automation → Tools → MCP → Linear

## Чеклист міграції на webhook

- [ ] Створити automation з тригером Webhook
- [ ] Увімкнути «Open pull request»
- [ ] Написати prompt (див. вище)
- [ ] Зберегти → скопіювати Webhook URL + API key
- [ ] GitHub Secrets: `CURSOR_AUTOMATION_WEBHOOK_URL`, `CURSOR_AUTOMATION_WEBHOOK_KEY`
- [ ] Оновити workflow: крок `curl` замість `cloud-agent-launch`
- [ ] Видалити або закоментувати `cloud-agent-launch` після перевірки
