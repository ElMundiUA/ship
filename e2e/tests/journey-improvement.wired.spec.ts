import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Сквозной сценарий: improvement через API → Accept в UI.
 *
 * @deployed
 */
test.describe("journey: improvement accept (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test("@deployed create via API, accept in console", async ({
    page,
    request,
  }) => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)",
    );

    const ws = await shipResolveWorkspaceId(request);
    const marker = `e2e-imp-${Date.now()}`;
    const create = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/improvements`,
      {
        kind: "test",
        title: `${marker} Add retries to callback`,
        body: "E2E seeded improvement for UI accept flow.",
        impact: "low",
        effort: "low",
        context: { e2e: true },
      },
    );
    expect(create.ok(), `create improvement ${create.status()}`).toBeTruthy();

    await page.goto("/improvements");
    await expect(
      page.getByRole("heading", { name: "Improvements" }),
    ).toBeVisible({ timeout: 30_000 });
    const row = page.locator("li").filter({ hasText: marker });
    await expect(row).toBeVisible({ timeout: 15_000 });

    await row.getByRole("button", { name: "Accept" }).click();

    await expect(page).toHaveURL(/decided_accepted/, { timeout: 20_000 });
    await expect(page.getByText("Marked as accepted.")).toBeVisible();
  });
});
