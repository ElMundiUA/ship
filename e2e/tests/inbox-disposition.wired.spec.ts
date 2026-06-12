import { expect, test } from "@playwright/test";

import {
  findInboxItemIdByTitle,
  mintInboxItem,
} from "../lib/inbox-helpers";
import {
  hasShipApiCredentials,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Deployed e2e: the `/approve/{id}` confirm page (ELS-294 — the
 * console's only remaining inbox surface after the MCP-first rework).
 *
 * Covers: ordinary approve / reject / resolve / dismiss, the
 * destructive typed-slug confirm (web-only by stakes policy), and the
 * read-only terminal state. Day-to-day inbox triage happens over MCP
 * and Telegram; this page is the deep-link target those emit.
 *
 * Requires: E2E_STORAGE_STATE, E2E_SHIP_API_BASE, E2E_SHIP_API_TOKEN (admin).
 *
 * @deployed
 */
test.describe("approve page: dispositions (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  const wired = hasPlaywrightStorageState() && hasShipApiCredentials();

  let workspaceId = "";
  let resolvedItemId = "";

  async function openApprove(
    page: import("@playwright/test").Page,
    itemId: string,
  ) {
    await page.goto(`/approve/${encodeURIComponent(itemId)}`);
    await expect(page.getByTestId("approve-card")).toBeVisible({
      timeout: 30_000,
    });
  }

  test("@deployed resolve persists after reload", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    workspaceId = await shipResolveWorkspaceId(request);
    const title = `e2e-approve-resolve-${Date.now()}`;
    await mintInboxItem(request, workspaceId, {
      type: "blocker",
      title,
      summary: "approve-page resolve path",
    });
    const itemId = await findInboxItemIdByTitle(request, workspaceId, title);
    resolvedItemId = itemId;

    await openApprove(page, itemId);
    await page.getByRole("button", { name: "Mark handled" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      page.getByRole("button", { name: "Mark handled" }),
    ).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId("approve-resolved")).toBeVisible();
  });

  test("@deployed dismiss persists after reload", async ({ page, request }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-approve-dismiss-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "blocker",
      title,
      summary: "approve-page dismiss path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openApprove(page, itemId);
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Dismiss" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("dismissed").first()).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("approve-resolved")).toBeVisible();
  });

  test("@deployed approve reaches approved terminal", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-approve-approve-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "approval",
      title,
      summary: "approve-page approve path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openApprove(page, itemId);
    await page.getByRole("button", { name: "Approve", exact: true }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("approved").first()).toBeVisible();
  });

  test("@deployed reject reaches rejected terminal", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-approve-reject-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "approval",
      title,
      summary: "approve-page reject path",
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openApprove(page, itemId);
    await page.getByRole("button", { name: "Reject" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("rejected").first()).toBeVisible();
  });

  test("@deployed destructive approval demands the typed slug", async ({
    page,
    request,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");

    const ws = workspaceId || (await shipResolveWorkspaceId(request));
    const title = `e2e-approve-destructive-${Date.now()}`;
    await mintInboxItem(request, ws, {
      type: "approval",
      title,
      summary: "approve-page destructive path",
      payload: { stakes: "destructive" },
    });
    const itemId = await findInboxItemIdByTitle(request, ws, title);

    await openApprove(page, itemId);
    // No bare Approve button — the typed-slug form replaces it.
    const form = page.getByTestId("approve-destructive-form");
    await expect(form).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve", exact: true }),
    ).toHaveCount(0);

    await form.getByPlaceholder('type "approve"').fill("approve");
    await form.getByRole("button", { name: "Confirm approve" }).click();

    await expect(page.getByTestId("approve-resolved")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("approved").first()).toBeVisible();
  });

  test("@deployed terminal item hides disposition buttons", async ({
    page,
  }) => {
    test.skip(!wired, "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN");
    test.skip(!resolvedItemId, "depends on resolve scenario");

    await openApprove(page, resolvedItemId);
    await expect(page.getByTestId("approve-resolved")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Mark handled" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Dismiss" })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Approve", exact: true }),
    ).toHaveCount(0);
  });
});
