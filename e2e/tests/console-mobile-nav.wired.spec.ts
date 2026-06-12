import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

test.describe("console mobile nav (wired)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test("menu button opens drawer with Inbox link", async ({ page }) => {
    await page.goto("/");
    const menu = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menu).toBeVisible({ timeout: 30_000 });
    await menu.click();
    await expect(page.getByRole("link", { name: /Inbox/ }).first()).toBeVisible();
  });

  test("Escape closes drawer and returns focus to menu button", async ({
    page,
  }) => {
    await page.goto("/inbox");
    const menu = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menu).toBeVisible({ timeout: 30_000 });
    await menu.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(menu).toBeFocused();
  });
});

test.describe("console desktop nav regression (wired)", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test("sticky sidebar visible without mobile menu button", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("button", { name: "Open navigation menu" }),
    ).toBeHidden();
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible({
      timeout: 30_000,
    });
  });
});
