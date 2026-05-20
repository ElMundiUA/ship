import * as fs from "fs";
import * as path from "path";

import { expect, test } from "@playwright/test";

const SNAPSHOT_DIR = path.join(
  __dirname,
  "inbox-mailbox.visual.public.spec.ts-snapshots",
);

/**
 * Inbox mailbox visual regression (ELS-146) — deterministic fixture route.
 * Console must run with `SHIP_E2E_INBOX_VISUAL=1`.
 * Update baselines: `E2E_UPDATE_SNAPSHOTS=1 npx playwright test inbox-mailbox.visual.public`.
 */
test.describe("inbox mailbox visuals (fixture)", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test.beforeEach(() => {
    test.skip(
      process.env.SHIP_E2E_INBOX_VISUAL !== "1",
      "Start console with SHIP_E2E_INBOX_VISUAL=1 (see e2e/README.md)",
    );
  });

  test("empty inbox shows EmptyState copy", async ({ page }) => {
    await page.goto("/e2e/inbox-mailbox?variant=empty");
    await expect(page.getByText("Inbox empty", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Nothing waiting on you — agents working."),
    ).toBeVisible();

    const hasBaseline =
      fs.existsSync(SNAPSHOT_DIR) &&
      fs.readdirSync(SNAPSHOT_DIR).some((name) => name.startsWith("inbox-empty"));
    test.skip(
      !hasBaseline && !process.env.E2E_UPDATE_SNAPSHOTS,
      `Missing baseline in ${SNAPSHOT_DIR} — run with E2E_UPDATE_SNAPSHOTS=1 once`,
    );
    await expect(page).toHaveScreenshot("inbox-empty.png", {
      maxDiffPixelRatio: 0.02,
    });
  });

  test("mixed list renders glyph kickers for all types", async ({ page }) => {
    await page.goto("/e2e/inbox-mailbox?variant=mixed");
    const list = page.getByTestId("inbox-mailbox-rows");
    await expect(list).toBeVisible();

    await expect(list.getByText("? CLARIFY").first()).toBeVisible();
    await expect(list.getByText("! BLOCKER").first()).toBeVisible();
    await expect(list.getByText("≡ REPORT").first()).toBeVisible();
    await expect(list.getByText("★ ATTENTION").first()).toBeVisible();
    await expect(
      list.getByText("ELS-99 validation bounced — restart or skip?"),
    ).toBeVisible();

    const selected = list.locator('a[aria-current="page"]');
    await expect(selected).toHaveCount(1);

    const hasBaseline =
      fs.existsSync(SNAPSHOT_DIR) &&
      fs.readdirSync(SNAPSHOT_DIR).some((name) =>
        name.startsWith("inbox-mixed-list"),
      );
    test.skip(
      !hasBaseline && !process.env.E2E_UPDATE_SNAPSHOTS,
      `Missing baseline in ${SNAPSHOT_DIR} — run with E2E_UPDATE_SNAPSHOTS=1 once`,
    );
    await expect(page).toHaveScreenshot("inbox-mixed-list.png", {
      maxDiffPixelRatio: 0.02,
    });
  });
});
