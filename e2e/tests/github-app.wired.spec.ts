import { expect, test } from "@playwright/test";

import { completeGitHubAppInstallWizard } from "../lib/github-install";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * End-to-end: Ship console → GitHub App install wizard → callback back to console.
 *
 * Регистрацию в Ship не автоматизируем: нужен готовый `E2E_STORAGE_STATE`
 * (один раз залогиниться и сохранить storage).
 *
 * Включается явно:  E2E_RUN_GITHUB_APP_INSTALL=1
 *
 * Рекомендуется добавить в storageState сессию **github.com** (залогиниться
 * в том же codegen-браузере), тогда пароль на GitHub не нужен. Альтернатива:
 * E2E_GITHUB_USERNAME + E2E_GITHUB_PASSWORD (бот без 2FA).
 *
 * Опционально:
 *   E2E_GITHUB_INSTALL_ACCOUNT — имя аккаунта/орг на шаге выбора
 *   E2E_GITHUB_REPO_FULL_NAME  — owner/name для «Only select repositories»
 */

test.describe("GitHub App install (wired)", () => {
  test.describe.configure({ timeout: 180_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_GITHUB_APP_INSTALL !== "1",
      "Set E2E_RUN_GITHUB_APP_INSTALL=1 to run GitHub App install automation",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE to a valid Playwright storage JSON",
    );
  });

  test("click Install in console, finish wizard on GitHub, land back on onboarding", async ({
    page,
    baseURL,
  }) => {
    const consoleRe =
      typeof baseURL === "string"
        ? new RegExp(baseURL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
        : /localhost|127\.0\.0\.1/;

    await page.goto("/onboarding?step=github");

    await expect(page.getByTestId("onboarding-install-github")).toBeVisible({
      timeout: 30_000,
    });

    const nav = page.waitForURL(/github\.com/, { timeout: 60_000 });
    await page.getByTestId("onboarding-install-github").click();
    await nav;

    await completeGitHubAppInstallWizard(page, { consoleOrigin: consoleRe });

    await expect(page).toHaveURL(/\/onboarding/i, { timeout: 30_000 });
    await expect(
      page
        .getByRole("heading", { name: /which repos should ship watch/i })
        .or(page.getByRole("heading", { name: /pick a tracker/i }))
        .or(page.getByTestId("onboarding-done-title")),
    ).toBeVisible({ timeout: 60_000 });
  });
});
