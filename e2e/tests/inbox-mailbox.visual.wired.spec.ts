import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Inbox mailbox visual regression (ELS-146).
 * Requires E2E_STORAGE_STATE; snapshots are updated via
 * `npx playwright test inbox-mailbox.visual --update-snapshots`.
 */
test.describe("inbox mailbox visuals (wired)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test.use({ viewport: { width: 1280, height: 900 } });

  test("empty inbox shows EmptyState copy", async ({ page }) => {
    await page.goto("/inbox?ownership=mine");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    const emptyTitle = page.getByText("Inbox empty", { exact: true });
    if (await emptyTitle.isVisible().catch(() => false)) {
      await expect(
        page.getByText("Nothing waiting on you — agents working."),
      ).toBeVisible();
      await expect(page).toHaveScreenshot("inbox-empty.png", {
        maxDiffPixelRatio: 0.02,
      });
    }
  });

  test("inbox list renders glyph kickers when items exist", async ({ page }) => {
    await page.goto("/inbox?ownership=all");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    const rows = page.locator('a[href*="selected="]');
    const count = await rows.count();
    test.skip(count < 1, "No inbox rows in workspace — seed items for snapshot");

    await expect(rows.first()).toHaveAttribute("aria-current", /page|/);
    await expect(page).toHaveScreenshot("inbox-mixed-list.png", {
      maxDiffPixelRatio: 0.02,
    });
  });
});
