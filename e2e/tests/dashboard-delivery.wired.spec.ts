import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Узкий UI-слой поверх dashboard: «Recommended actions» и блоки недавних прогонов.
 * Требует онбординг + бэкенд с дашбордом (не mock-only).
 *
 * Phase-1 two-mode shell: `/` больше не рендерит DashboardLive,
 * дашборд переедет под `/r/<owner>/<repo>` в PR-4. Тест на паузе
 * до того момента — заодно ещё раз перепроверим, что переехавший
 * вид сохранил семантику "Recommended actions" + run-strips.
 */
test.describe.skip("dashboard delivery signals (wired) — pending PR-4 migration", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test("home shows recommended actions and run strips", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Operating dashboard", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await expect(page.getByText(/Recommended actions/i).first()).toBeVisible({
      timeout: 15_000,
    });

    const wf = page.getByRole("heading", {
      name: "Recent workflow runs",
      exact: true,
    });
    const pip = page.getByRole("heading", {
      name: "Recent pipeline runs",
      exact: true,
    });
    await expect(wf.or(pip).first()).toBeVisible({ timeout: 15_000 });
  });
});
