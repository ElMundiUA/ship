#!/usr/bin/env node
/**
 * One-off / reusable: create ElMundi pre-release Linear issues (known E2E-on-dev gaps).
 * Includes: billing.subscribed, catalog keyboard, tag filter, audio-access, Paddle overlay.
 * Re-running may create duplicates — search Linear before repeat.
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = resolve(__dirname, "..");
const ENV_PATH = resolve(AGENT_DIR, ".env");
const LINEAR_API = "https://api.linear.app/graphql";

const PROJECT_ID = "2eead1a7-8585-4678-96e9-6b3f86b6534c"; // ElMundi pre-release
const TEAM_ID = "98facea5-36fb-44a5-bb47-c651f3ee4073"; // ELM
const STATE_ID = "e2e6345e-dbb4-4423-a68f-5b51cf99d57e"; // Backlog
const BUG_LABEL_ID = "37f890ba-e654-4d2d-811b-06a27777286c"; // Bug

function loadEnv() {
  if (!existsSync(ENV_PATH)) throw new Error(".env not found");
  const env = {};
  for (const line of readFileSync(ENV_PATH, "utf8").split("\n")) {
    const m = line.match(/^([^#=]+)=(.*)$/);
    if (m) env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return env;
}

async function graphql(apiKey, query, variables = {}) {
  const res = await fetch(LINEAR_API, {
    method: "POST",
    headers: { Authorization: apiKey, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors) throw new Error(JSON.stringify(json.errors, null, 2));
  return json.data;
}

const ISSUES = [
  {
    title: "[E2E dev] billing.subscribed: нет кнопки Manage Subscription на /billing",
    description: `## Контекст
Регрессия Playwright против **https://dev.elmundi.com** (\`PLAYWRIGHT_BASE_URL\` + \`MAILOSAUR_*\`, \`E2E_SUBSCRIBER_EMAIL\`).

## Файл
\`website/tests/e2e/regression/billing.subscribed.functional.spec.ts\`

## Симптом
После OTP-логина подписчика не находится \`getByRole('button', { name: 'Manage Subscription' })\` — таймаут 30s.

## Гипотезы
- У \`E2E_SUBSCRIBER_EMAIL\` на dev нет активной подписки в Paddle / рассинхрон с API \`/api/users/me/subscription\`.
- Другой UI для состояния биллинга на dev.

## AC
- [ ] Под тестовым подписчиком на dev на \`/billing\` видна активная подписка и CTA «Manage Subscription» (или обновить тест/фикстуру под фактический UI).
`,
  },
  {
    title: "[E2E dev] catalog.functional: клавиатура — не уходит на /category/…",
    description: `## Контекст
Регрессия против **dev.elmundi.com**.

## Файл
\`website/tests/e2e/regression/catalog.functional.spec.ts\` — тест **cards are keyboard-activatable**.

## Симптом
После активации с клавиатуры \`toHaveURL(/\\/category\\//)\` падает: остаётся \`https://dev.elmundi.com/\`.

## Гипотезы
- Фокус / roving tabindex / Enter не совпадают с локальным поведением.
- Тайминг или отличие сборки на dev.

## AC
- [ ] Клавиатурная активация первой карточки каталога ведёт на страницу категории на dev (или скорректировать тест под реальный a11y-поток).
`,
  },
  {
    title: "[E2E dev] category-tag-filter: после выбора тега URL остаётся на /",
    description: `## Контекст
Регрессия против **dev.elmundi.com**.

## Файл
\`website/tests/e2e/regression/category-tag-filter.functional.spec.ts\`

## Симптом
Ожидался URL категории, фактически остаётся главная (\`/\`).

## Гипотезы
- Нет подходящих тегов/данных в каталоге на dev для сценария.
- Регрессия в навигации при фильтре тегов.

## AC
- [ ] Сценарий «тег → категория с отфильтрованным плейлистом» работает на dev или тест привязан к стабильным фикстурам.
`,
  },
  {
    title: "[E2E dev] subscribed-user.audio-access: 403 и модалка перекрывает каталог",
    description: `## Контекст
Регрессия против **dev.elmundi.com** (\`E2E_SUBSCRIBER_EMAIL\`).

## Файл
\`website/tests/e2e/regression/subscribed-user.audio-access.spec.ts\`

## Симптом
- В консоли: **403** на ресурс (CDN / signed URL).
- Клик по карточке каталога: \`auth_modal\` перехватывает pointer (\`plan__features\` / подписка), таймаут на \`openFirstCard\`.

## Гипотезы
- Bunny / entitlement на dev для подписчика.
- Автооткрытие subscription modal после логина мешает сценарию.

## AC
- [ ] Подписчик на dev без paywall на контенте с аудио; signed URL не 403 для entitled эпизода.
- [ ] E2E стабильно (закрытие модалки / ожидание) или исправлен бэкенд/CDN.
`,
  },
  {
    title: "[E2E dev] Paddle: после клика Start Free Trial на /pricing не открывается overlay (Playwright)",
    description: `## Контекст
После тюнинга paddle-checkout.sandbox.functional.spec.ts (PLAYWRIGHT_BASE_URL, ожидание профиля, расширенный матч URL Paddle, .first() для CTA) прогон против **https://dev.elmundi.com** может падать: нет iframe и нет подходящего network request.

## Файл
\`website/tests/e2e/regression/paddle-checkout.sandbox.functional.spec.ts\`

## Гипотезы
- В Paddle Checkout (sandbox) не добавлен origin **https://dev.elmundi.com**
- Ошибка /api/paddle/token или price IDs на dev
- Другой host у overlay, чем ловит тест

## AC
- [ ] Sandbox checkout открывается с dev после OTP (Mailosaur) + клик по annual CTA
- [ ] E2E зелёный с PLAYWRIGHT_BASE_URL=https://dev.elmundi.com или задокументировано исключение
`,
  },
];

async function main() {
  const env = loadEnv();
  const apiKey = env.LINEAR_API_KEY;
  if (!apiKey) throw new Error("LINEAR_API_KEY not set");

  const mutation = `
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { identifier title url }
      }
    }
  `;

  for (const item of ISSUES) {
    const data = await graphql(apiKey, mutation, {
      input: {
        teamId: TEAM_ID,
        projectId: PROJECT_ID,
        stateId: STATE_ID,
        title: item.title,
        description: item.description,
        labelIds: [BUG_LABEL_ID],
      },
    });
    const issue = data.issueCreate?.issue;
    if (!issue) throw new Error("issueCreate failed: " + JSON.stringify(data));
    console.log(issue.identifier, issue.url);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
