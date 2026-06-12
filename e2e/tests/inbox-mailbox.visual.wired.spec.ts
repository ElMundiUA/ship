import { expect, test } from "@playwright/test";

import { mintInboxItem } from "../lib/inbox-helpers";
import { hasShipApiCredentials, shipResolveWorkspaceId } from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

// Only types the seed endpoint accepts (InboxItemIn literal — ELS-144
// dropped "failure"/"stuck" from the write surface; those rows are
// minted by internal flows only). approval/improvement/exception all
// render the ★ ATTENTION kicker, so glyph coverage is unchanged.
const INBOX_TYPES = [
  "clarification",
  "improvement",
  "approval",
  "exception",
  "blocker",
  "report",
] as const;

/**
 * Live inbox mailbox integration checks (ELS-146).
 * Visual baselines live in `inbox-mailbox.visual.public.spec.ts`.
 */
test.describe("inbox mailbox visuals (wired)", () => {
  const wired = hasPlaywrightStorageState() && hasShipApiCredentials();

  test.beforeEach(() => {
    test.skip(!hasPlaywrightStorageState(), "Set E2E_STORAGE_STATE (see e2e/README.md)");
  });

  test.use({ viewport: { width: 1280, height: 900 } });

  test("inbox list renders glyph kickers after API seed", async ({ page, request }) => {
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

    const list = page.getByTestId("inbox-mailbox-rows");
    await expect(list.getByText("? CLARIFY").first()).toBeVisible();
    await expect(list.getByText("! BLOCKER").first()).toBeVisible();
    await expect(list.getByText("≡ REPORT").first()).toBeVisible();
    await expect(list.getByText("★ ATTENTION").first()).toBeVisible();

    const selected = rows.filter({ has: page.locator('[aria-current="page"]') });
    if ((await selected.count()) > 0) {
      await expect(selected.first()).toHaveAttribute("aria-current", "page");
    }
  });
});
