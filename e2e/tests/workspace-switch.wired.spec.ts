/**
 * Sidebar workspace switcher — chip tracks ?ws= after dropdown selection.
 *
 * @deployed
 */
import { expect, test } from "@playwright/test";

import { hasShipApiCredentials, shipApiPost } from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiWorkspace = { id: string; name: string; slug: string };

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

function uniqueSlug(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function createThrowawayWorkspace(
  request: Parameters<typeof shipApiPost>[0],
  label: string,
): Promise<ApiWorkspace> {
  const slug = uniqueSlug("e2e-ws-switch");
  const res = await shipApiPost(request, "/v1/workspaces", {
    name: `${label} ${slug}`,
    slug,
  });
  expect(res.ok(), `POST /v1/workspaces → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as ApiWorkspace;
  expect(body.id).toBeTruthy();
  expect(body.slug).toBe(slug);
  return body;
}

test.describe("workspace switcher (wired)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test("@deployed sidebar chip updates after dropdown switch", async ({
    page,
    request,
  }) => {
    const wsA = await createThrowawayWorkspace(request, "E2E switch A");
    const wsB = await createThrowawayWorkspace(request, "E2E switch B");

    await page.goto(`/inbox?ws=${encodeURIComponent(wsA.id)}`);
    await expect(page.getByTitle("Switch workspace")).toContainText(wsA.name, {
      timeout: 30_000,
    });

    await page.getByTitle("Switch workspace").click();
    await page.getByRole("link", { name: new RegExp(wsB.name) }).click();

    await expect(page).toHaveURL(
      new RegExp(`[?&]ws=${encodeURIComponent(wsB.id)}`),
      { timeout: 20_000 },
    );
    await expect(page.getByTitle("Switch workspace")).toContainText(wsB.name);
    await expect(page.getByTitle("Switch workspace")).toContainText(wsB.slug);
  });

  test("@deployed round-trip A → B → A keeps chip in sync", async ({
    page,
    request,
  }) => {
    const wsA = await createThrowawayWorkspace(request, "E2E round A");
    const wsB = await createThrowawayWorkspace(request, "E2E round B");

    await page.goto(`/inbox?ws=${encodeURIComponent(wsA.id)}`);
    await expect(page.getByTitle("Switch workspace")).toContainText(wsA.name, {
      timeout: 30_000,
    });

    const chip = page.getByTitle("Switch workspace");

    await chip.click();
    await page.getByRole("link", { name: new RegExp(wsB.name) }).click();
    await expect(chip).toContainText(wsB.name, { timeout: 20_000 });

    await chip.click();
    await page.getByRole("link", { name: new RegExp(wsA.name) }).click();
    await expect(chip).toContainText(wsA.name, { timeout: 20_000 });
  });
});
