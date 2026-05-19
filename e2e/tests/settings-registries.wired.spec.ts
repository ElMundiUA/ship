/**
 * Workspace registries settings — empty state, UI create, URL validation.
 *
 * @deployed
 */
import { expect, test, type Page } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiDelete,
  shipApiGet,
  shipApiPost,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiWorkspace = { id: string; slug: string };
type ApiArtifactRepo = { id: string; url: string };

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

function uniqueSlug(): string {
  return `e2e-ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function createThrowawayWorkspace(
  request: Parameters<typeof shipApiPost>[0],
): Promise<ApiWorkspace> {
  const slug = uniqueSlug();
  const res = await shipApiPost(request, "/v1/workspaces", {
    name: `E2E registries ${slug}`,
    slug,
  });
  expect(res.ok(), `POST /v1/workspaces → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as ApiWorkspace;
  expect(body.id).toBeTruthy();
  expect(body.slug).toBe(slug);
  return body;
}

async function openRegistries(page: Page, workspaceId: string): Promise<void> {
  await page.goto(`/settings/registries?ws=${encodeURIComponent(workspaceId)}`);
  await expect(
    page.getByRole("heading", { name: "Registries", exact: true }),
  ).toBeVisible({ timeout: 30_000 });
}

async function expandAddRepoForm(page: Page): Promise<void> {
  await page.getByText("+ Add repo").click();
}

test.describe("settings: registries (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  const createdRepoIds: { wsId: string; repoId: string }[] = [];

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test.afterEach(async ({ request }) => {
    for (const { wsId, repoId } of [...createdRepoIds]) {
      try {
        const res = await shipApiDelete(
          request,
          `/v1/workspaces/${encodeURIComponent(wsId)}/artifact-repos/${encodeURIComponent(repoId)}`,
        );
        expect(res.ok() || res.status() === 204).toBeTruthy();
      } catch {
        // Best-effort cleanup for throwaway workspace.
      }
    }
    createdRepoIds.length = 0;
  });

  test("@deployed registries tab shows empty state", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    await openRegistries(page, ws.id);

    await expect(
      page.getByText(/No artifact repos registered yet/),
    ).toBeVisible();
    await expect(page.getByText("+ Add repo")).toBeVisible();
  });

  test("@deployed UI create registers file:// repo", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    const fileUrl = `file:///tmp/e2e-registries-${Date.now()}`;

    await openRegistries(page, ws.id);
    await expandAddRepoForm(page);
    await page.locator('input[name="url"]').fill(fileUrl);
    await page.getByRole("button", { name: "Register" }).click();

    await expect(page).toHaveURL(/\/settings/, { timeout: 20_000 });

    await page.goto(`/settings/registries?ws=${encodeURIComponent(ws.id)}`);
    await expect(page.getByText(fileUrl, { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    const listed = await shipApiGet(
      request,
      `/v1/workspaces/${encodeURIComponent(ws.id)}/artifact-repos`,
    );
    expect(listed.ok(), `GET artifact-repos → ${listed.status()}`).toBeTruthy();
    const rows = (await listed.json()) as ApiArtifactRepo[];
    const created = rows.find((r) => r.url === fileUrl);
    expect(created?.id).toBeTruthy();
    createdRepoIds.push({ wsId: ws.id, repoId: created!.id });
  });

  test("@deployed invalid URL shows validation banner", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);

    await openRegistries(page, ws.id);
    await expandAddRepoForm(page);
    await page.locator('input[name="url"]').fill("not-a-url");
    await page.getByRole("button", { name: "Register" }).click();

    await expect(page).toHaveURL(/error=invalid_url/, { timeout: 20_000 });
    await expect(
      page.getByText(/That URL doesn’t look right/),
    ).toBeVisible({ timeout: 15_000 });
  });
});
