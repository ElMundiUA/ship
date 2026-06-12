import { expect, test } from "@playwright/test";

import { findInboxItemIdByTitle } from "../lib/inbox-helpers";
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

    // MCP-first rework: the mailbox is gone — accept on the item's
    // /approve/{id} page instead.
    const itemId = await findInboxItemIdByTitle(request, ws, marker, {
      match: "contains",
    });
    await page.goto(`/approve/${encodeURIComponent(itemId)}`);
    await expect(page.getByTestId("approve-card")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("button", { name: "Accept" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
  });
});
