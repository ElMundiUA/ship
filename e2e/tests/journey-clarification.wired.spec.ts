import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Сквозной сценарий: создать clarification через API → ответить в UI.
 * Токен должен быть **admin** (POST /clarifications).
 *
 * @deployed
 */
test.describe("journey: clarification answer (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test("@deployed create via API, answer in console", async ({
    page,
    request,
  }) => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)",
    );

    const ws = await shipResolveWorkspaceId(request);
    const marker = `e2e-clarify-${Date.now()}`;
    const create = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/clarifications`,
      {
        question: `${marker} — what is the business goal?`,
        context: { e2e: true },
      },
    );
    expect(create.ok(), `create clarification ${create.status()}`).toBeTruthy();

    await page.goto("/inbox?type=clarification");
    await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible({
      timeout: 30_000,
    });
    const row = page.locator("li").filter({ hasText: marker });
    await expect(row).toBeVisible({ timeout: 15_000 });

    await row.getByPlaceholder("Answer the agent's question…").fill(
      "E2E: approved — ship validation.",
    );
    await row.getByRole("button", { name: "Send answer" }).click();

    await expect(page).toHaveURL(/banner=answered/, { timeout: 20_000 });
    await expect(page.getByText("Answer recorded.")).toBeVisible();
  });
});
