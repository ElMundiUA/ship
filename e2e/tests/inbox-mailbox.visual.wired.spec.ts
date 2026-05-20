import * as fs from "fs";
import * as path from "path";

import { expect, test } from "@playwright/test";

import { mintInboxItem } from "../lib/inbox-helpers";
import { hasShipApiCredentials, shipResolveWorkspaceId } from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

const SNAPSHOT_DIR = path.join(
  __dirname,
  "inbox-mailbox.visual.wired.spec.ts-snapshots",
);

const INBOX_TYPES = [
  "clarification",
  "improvement",
  "failure",
  "approval",
  "exception",
  "stuck",
  "blocker",
  "report",
] as const;

/**
 * Inbox mailbox visual regression (ELS-146).
 * Requires E2E_STORAGE_STATE + Ship API credentials to seed rows.
 * Update baselines: `npx playwright test inbox-mailbox.visual --update-snapshots`.
 */
test.describe("inbox mailbox visuals (wired)", () => {
  const wired = hasPlaywrightStorageState() && hasShipApiCredentials();

  test.beforeEach(() => {
    test.skip(!hasPlaywrightStorageState(), "Set E2E_STORAGE_STATE (see e2e/README.md)");
  });

  test.use({ viewport: { width: 1280, height: 900 } });

  test("empty inbox shows EmptyState copy", async ({ page }) => {
    await page.goto("/inbox?ownership=mine");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    const emptyTitle = page.getByText("Inbox empty", { exact: true });
    test.skip(
      !(await emptyTitle.isVisible().catch(() => false)),
      "Workspace has open inbox rows — seed an empty mine inbox or use a dedicated E2E workspace",
    );
    await expect(
      page.getByText("Nothing waiting on you — agents working."),
    ).toBeVisible();
    const baseline = path.join(SNAPSHOT_DIR, "inbox-empty.png");
    test.skip(
      !fs.existsSync(baseline) && !process.env.E2E_UPDATE_SNAPSHOTS,
      `Missing baseline ${baseline} — run with E2E_UPDATE_SNAPSHOTS=1 once`,
    );
    await expect(page).toHaveScreenshot("inbox-empty.png", {
      maxDiffPixelRatio: 0.02,
    });
  });

  test("inbox list renders glyph kickers for all types", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const workspaceId = await shipResolveWorkspaceId(request);
    const stamp = Date.now();
    for (const type of INBOX_TYPES) {
      await mintInboxItem(request, workspaceId, {
        type,
        title: `e2e-els146-${type}-${stamp}`,
        summary: `visual fixture ${type}`,
      });
    }

    await page.goto("/inbox?ownership=all");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    const rows = page.locator('[data-testid="inbox-mailbox-rows"] a[href*="selected="]');
    await expect(rows).not.toHaveCount(0, { timeout: 30_000 });

    const selected = rows.filter({ has: page.locator('[aria-current="page"]') });
    if ((await selected.count()) > 0) {
      await expect(selected.first()).toHaveAttribute("aria-current", "page");
    }

    const list = page.getByTestId("inbox-mailbox-rows");
    await expect(list.getByText("? CLARIFY").first()).toBeVisible();
    await expect(list.getByText("! BLOCKER").first()).toBeVisible();
    await expect(list.getByText("≡ REPORT").first()).toBeVisible();
    await expect(list.getByText("★ ATTENTION").first()).toBeVisible();

    const baseline = path.join(SNAPSHOT_DIR, "inbox-mixed-list.png");
    test.skip(
      !fs.existsSync(baseline) && !process.env.E2E_UPDATE_SNAPSHOTS,
      `Missing baseline ${baseline} — run with E2E_UPDATE_SNAPSHOTS=1 once`,
    );
    await expect(page).toHaveScreenshot("inbox-mixed-list.png", {
      maxDiffPixelRatio: 0.02,
    });
  });
});
