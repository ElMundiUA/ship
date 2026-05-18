import { expect, test } from "@playwright/test";

import {
  findInboxItemIdByTitle,
  getInboxItemDetail,
  listWorkspaceMembers,
  mintInboxItem,
} from "../lib/inbox-helpers";
import {
  hasShipApiCredentials,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Deployed e2e: /inbox/[id] disposition forms (resolve, dismiss, approve,
 * reject, snooze, reassign, comment) + terminal-state guard.
 *
 * Requires: E2E_STORAGE_STATE, E2E_SHIP_API_BASE, E2E_SHIP_API_TOKEN (admin).
 *
 * @deployed
 */
test.describe("inbox: disposition detail (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  const wired =
    hasPlaywrightStorageState() && hasShipApiCredentials();

  let workspaceId = "";
  let resolvedItemId = "";

  async function openInboxDetail(
    page: import("@playwright/test").Page,
    itemId: string,
  ) {
    await page.goto(`/inbox/${itemId}`);
    await expect(page).toHaveURL(new RegExp(`/inbox/${itemId}`), {
      timeout: 30_000,
    });
  }

  test("@deployed resolve persists after reload", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    workspaceId = await shipResolveWorkspaceId(request);
    const title = `e2e-inbox-resolve-${Date.now()}`;
    await mintInboxItem(request, workspaceId, {
      type: "blocker",
      title,
      summary: "ELS-114 resolve path",
    });
    const itemId = await findInboxItemIdByTitle(request, workspaceId, title);
    resolvedItemId = itemId;

    await openInboxDetail(page, itemId);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await page.getByRole("button", { name: "Mark handled" }).click();

    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      page.getByText("Resolved", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Mark handled" })).toHaveCount(
      0,
    );

    await page.reload();
    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible();
    await expect(
      page.getByText("Resolved", { exact: true }).first(),
    ).toBeVisible();
  });

  test("@deployed dismiss persists after reload", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-inbox-dismiss-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "blocker",
      title,
      summary: "ELS-114 dismiss path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openInboxDetail(page, itemId);
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Dismiss" }).click();

    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      page.getByText("Dismissed", { exact: true }).first(),
    ).toBeVisible();

    await page.reload();
    await expect(
      page.getByText("Dismissed", { exact: true }).first(),
    ).toBeVisible();
  });

  test("@deployed approve reaches approved terminal", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-inbox-approve-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "approval",
      title,
      summary: "ELS-114 approve path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openInboxDetail(page, itemId);
    await page.getByRole("button", { name: "Approve" }).click();

    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator("code").filter({ hasText: "approved" })).toBeVisible();

    await page.reload();
    await expect(page.locator("code").filter({ hasText: "approved" })).toBeVisible();
  });

  test("@deployed reject reaches rejected terminal", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-inbox-reject-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "approval",
      title,
      summary: "ELS-114 reject path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openInboxDetail(page, itemId);
    await page.getByRole("button", { name: "Reject" }).click();

    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.locator("code").filter({ hasText: "rejected" })).toBeVisible();

    await page.reload();
    await expect(page.locator("code").filter({ hasText: "rejected" })).toBeVisible();
  });

  test("@deployed snooze shows wake-up and persists", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-inbox-snooze-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "blocker",
      title,
      summary: "ELS-114 snooze path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openInboxDetail(page, itemId);
    await page.getByRole("button", { name: "Snooze" }).click();

    await expect(
      page.getByRole("heading", { name: "Pick up where you left off" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByRole("button", { name: "Wake up now" }),
    ).toBeVisible();

    await page.reload();
    await expect(
      page.getByRole("button", { name: "Wake up now" }),
    ).toBeVisible();
  });

  test("@deployed reassign updates owner card", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const members = await listWorkspaceMembers(request, ws);
    if (members.length < 2) {
      test.skip(true, "workspace needs at least two members for reassign");
    }

    const title = `e2e-inbox-reassign-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "blocker",
      title,
      summary: "ELS-114 reassign path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    const detail = await getInboxItemDetail(request, ws, itemId);
    const currentEmail = detail.owner?.email?.toLowerCase() ?? "";

    const target = members.find(
      (m) => m.email.toLowerCase() !== currentEmail,
    );
    if (!target) {
      test.skip(true, "no alternate member distinct from current owner");
    }

    await openInboxDetail(page, itemId);
    const ownerCard = page.locator("aside").filter({ hasText: "Owner" });

    await ownerCard.locator('select[name="user_id"]').selectOption(target!.user_id);
    await ownerCard.getByRole("button", { name: "Reassign" }).click();

    await expect(ownerCard.getByText(target!.email)).toBeVisible({
      timeout: 20_000,
    });

    await page.reload();
    await expect(ownerCard.getByText(target!.email)).toBeVisible();
  });

  test("@deployed comment appears in activity", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-inbox-comment-${Date.now()}`;
    const marker = `e2e-comment-marker-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "blocker",
      title,
      summary: "ELS-114 comment path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    const before = await getInboxItemDetail(request, ws, itemId);
    const eventsBefore = (before.events ?? []).filter(
      (e) => !(e.action === "created"),
    ).length;

    await openInboxDetail(page, itemId);
    await page
      .getByPlaceholder("Leave context for the next person picking this up…")
      .fill(marker);
    await page.getByRole("button", { name: "Post comment" }).click();

    await expect(page.getByText(marker)).toBeVisible({ timeout: 20_000 });

    const after = await getInboxItemDetail(request, ws, itemId);
    const eventsAfter = (after.events ?? []).filter(
      (e) => !(e.action === "created"),
    ).length;
    expect(eventsAfter).toBeGreaterThanOrEqual(eventsBefore + 1);
  });

  test("@deployed terminal item hides disposition buttons", async ({
    page,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");
    test.skip(!resolvedItemId, "depends on resolve scenario");

    await openInboxDetail(page, resolvedItemId);
    await expect(page.getByRole("heading", { name: "Closed" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Mark handled" })).toHaveCount(
      0,
    );
    await expect(page.getByRole("button", { name: "Dismiss" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  });
});
