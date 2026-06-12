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

  // The hub "/" renders chrome without the header bar, so the menu
  // button lives on header'd pages — drive the drawer from Settings.
  test("menu button opens drawer with Chat link", async ({ page }) => {
    await page.goto("/settings");
    const menu = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menu).toBeVisible({ timeout: 30_000 });
    await menu.click();
    await expect(page.getByRole("link", { name: /Chat/ }).first()).toBeVisible();
  });

  test("Escape closes drawer and returns focus to menu button", async ({
    page,
  }) => {
    await page.goto("/settings");
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
    // Pin ?ws — bare "/" lands on the entry picker for multi-workspace
    // accounts, which renders no sidebar at all.
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    await expect(
      page.getByRole("button", { name: "Open navigation menu" }),
    ).toBeHidden();
    await expect(
      page.locator("aside nav").getByRole("link", { name: "Chat", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });
});
