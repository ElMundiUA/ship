import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

const DORA_CARD_TITLES = [
  "Deployment frequency",
  "Lead time for changes",
  "Change failure rate",
  "Mean time to recovery",
] as const;

/**
 * Wired regression for the /analytics DORA dashboard.
 * Requires E2E_STORAGE_STATE and a healthy DORA API (same contract as
 * console-flows.wired.spec.ts).
 */
test.describe("analytics DORA dashboard (wired)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test("loads analytics page with DORA cards and default 30-day window", async ({
    page,
  }) => {
    await page.goto("/analytics");
    await expect(
      page.getByRole("heading", { name: "Analytics", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    for (const title of DORA_CARD_TITLES) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }

    await expect(page.getByText(/DORA · last 30 days/)).toBeVisible();
  });

  test("window picker updates URL and DORA section header", async ({ page }) => {
    await page.goto("/analytics");
    await expect(
      page.getByRole("heading", { name: "Analytics", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByRole("link", { name: "90d", exact: true }).click();
    await expect(page).toHaveURL(/days=90/);
    await expect(page.getByText(/DORA · last 90 days/)).toBeVisible();

    await page.getByRole("link", { name: "180d", exact: true }).click();
    await expect(page).toHaveURL(/days=180/);
    await expect(page.getByText(/DORA · last 180 days/)).toBeVisible();
  });
});
