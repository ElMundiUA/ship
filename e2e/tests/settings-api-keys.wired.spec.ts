import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  listAuthTokens,
  revokeAuthToken,
  shipApiPost,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiWorkspace = { id: string; slug: string };

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

const PAT_SHAPE = /^ship_pat_[A-Za-z0-9_-]{20,}$/;

const BAD_INPUT_BANNER =
  "Missing or invalid form input. Try again with a real workspace, layer, and URL.";

function uniqueSlug(): string {
  return `e2e-ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function uniquePatName(): string {
  return `e2e-pat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function createThrowawayWorkspace(
  request: Parameters<typeof shipApiPost>[0],
): Promise<ApiWorkspace> {
  const slug = uniqueSlug();
  const res = await shipApiPost(request, "/v1/workspaces", {
    name: `E2E api-keys ${slug}`,
    slug,
  });
  expect(res.ok(), `POST /v1/workspaces → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as ApiWorkspace;
  expect(body.id).toBeTruthy();
  expect(body.slug).toBe(slug);
  return body;
}

function apiKeysUrl(workspaceId: string): string {
  return `/settings/api-keys?ws=${encodeURIComponent(workspaceId)}`;
}

/**
 * Workspace settings API keys: mint one-shot reveal, revoke, empty state, bad input.
 *
 * @deployed
 */
test.describe("settings: API keys (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  const mintedTokenIds: string[] = [];

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test.afterEach(async ({ request }) => {
    while (mintedTokenIds.length > 0) {
      const id = mintedTokenIds.pop()!;
      try {
        await revokeAuthToken(request, id);
      } catch {
        // best-effort teardown
      }
    }
  });

  test("@deployed empty state on PAT-free session", async ({ page, request }) => {
    const ws = await createThrowawayWorkspace(request);
    const listed = await listAuthTokens(request);
    test.skip(
      listed.length > 0,
      "empty-state copy only renders when the signed-in user has zero PATs",
    );

    await page.goto(apiKeysUrl(ws.id));
    await expect(
      page.getByRole("heading", { name: "API keys", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/No PATs yet\. Mint one below/i),
    ).toBeVisible();
    await expect(
      page.getByText("+ Create API key", { exact: true }),
    ).toBeVisible();
  });

  test("@deployed mint rejects empty name with bad_input banner", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);

    await page.goto(apiKeysUrl(ws.id));
    await expect(
      page.getByRole("heading", { name: "API keys", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByText("+ Create API key", { exact: true }).click();
    const nameInput = page.locator('input[name="name"]');
    await nameInput.evaluate((el) => el.removeAttribute("required"));
    await nameInput.fill("");
    await page.getByRole("button", { name: "Mint" }).click();

    await expect(page).toHaveURL(/error=bad_input/, { timeout: 20_000 });
    await expect(page.getByText(BAD_INPUT_BANNER)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("@deployed mint shows one-shot secret then hides after reload", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    const patName = uniquePatName();

    await page.goto(apiKeysUrl(ws.id));
    await expect(
      page.getByRole("heading", { name: "API keys", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByText("+ Create API key", { exact: true }).click();
    await page.locator('input[name="name"]').fill(patName);
    await page.getByRole("button", { name: "Mint" }).click();

    await expect(page).toHaveURL(/just_minted=1/, { timeout: 20_000 });
    await expect(
      page.getByRole("heading", { name: "API key created", exact: true }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText("Copy it now — the secret will never be shown again."),
    ).toBeVisible();

    const secretCode = page
      .locator("code")
      .filter({ hasText: /^ship_pat_/ })
      .first();
    await expect(secretCode).toBeVisible({ timeout: 15_000 });
    const secret = (await secretCode.textContent())?.trim() ?? "";
    expect(secret).toMatch(PAT_SHAPE);

    const rows = await listAuthTokens(request);
    const minted = rows.find((t) => t.name === patName);
    expect(minted?.id).toBeTruthy();
    mintedTokenIds.push(minted!.id);

    await page.goto(apiKeysUrl(ws.id));
    await expect(
      page.getByRole("heading", { name: "API key created", exact: true }),
    ).toHaveCount(0);
    await expect(secretCode).toHaveCount(0);
    await expect(page.getByText(secret, { exact: true })).toHaveCount(0);
  });

  test("@deployed revoke removes PAT row and API listing", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    const patName = uniquePatName();

    await page.goto(apiKeysUrl(ws.id));
    await expect(
      page.getByRole("heading", { name: "API keys", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByText("+ Create API key", { exact: true }).click();
    await page.locator('input[name="name"]').fill(patName);
    await page.getByRole("button", { name: "Mint" }).click();
    await expect(page).toHaveURL(/just_minted=1/, { timeout: 20_000 });

    const rowsAfterMint = await listAuthTokens(request);
    const minted = rowsAfterMint.find((t) => t.name === patName);
    expect(minted?.id).toBeTruthy();
    const tokenId = minted!.id;
    mintedTokenIds.push(tokenId);

    await page.goto(apiKeysUrl(ws.id));
    const row = page.locator("tr").filter({ hasText: patName });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: "Revoke" }).click();

    await expect(page).toHaveURL(/\/settings/, { timeout: 20_000 });
    await expect(row).toHaveCount(0, { timeout: 15_000 });

    const rowsAfterRevoke = await listAuthTokens(request);
    expect(rowsAfterRevoke.some((t) => t.id === tokenId)).toBe(false);
  });
});
