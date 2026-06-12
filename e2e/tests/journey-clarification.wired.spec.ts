import { expect, test } from "@playwright/test";

import { findInboxItemIdByTitle } from "../lib/inbox-helpers";
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

    // MCP-first rework: the mailbox is gone — the clarification is
    // answered on its /approve/{id} page (the deep-link target the
    // agent/Telegram would hand the operator).
    const itemId = await findInboxItemIdByTitle(request, ws, marker, {
      match: "contains",
    });
    await page.goto(`/approve/${encodeURIComponent(itemId)}`);
    await expect(page.getByTestId("approve-card")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(marker, { exact: false }).first()).toBeVisible();

    await page
      .getByPlaceholder("Reply to the agent's question…")
      .fill("E2E: approved — ship validation.");
    await page.getByRole("button", { name: "Answer & resolve" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
  });
});
