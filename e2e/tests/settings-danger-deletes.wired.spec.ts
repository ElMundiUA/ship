import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
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
    name: `E2E deletes ${slug}`,
    slug,
  });
  expect(res.ok(), `POST /v1/workspaces → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as ApiWorkspace;
  expect(body.id).toBeTruthy();
  expect(body.slug).toBe(slug);
  return body;
}

async function createArtifactRepo(
  request: Parameters<typeof shipApiPost>[0],
  workspaceId: string,
): Promise<ApiArtifactRepo> {
  const url = `https://github.com/e2e-deletes/placeholder-${Date.now()}`;
  const res = await shipApiPost(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/artifact-repos`,
    { kind: "workspace", url },
  );
  expect(res.ok(), `POST artifact-repos → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as ApiArtifactRepo;
  expect(body.id).toBeTruthy();
  expect(body.url).toBe(url);
  return body;
}

async function workspaceListed(
  request: Parameters<typeof shipApiGet>[0],
  workspaceId: string,
): Promise<boolean> {
  const res = await shipApiGet(request, "/v1/workspaces");
  expect(res.ok(), `GET /v1/workspaces → ${res.status()}`).toBeTruthy();
  const rows = (await res.json()) as { id: string }[];
  return rows.some((w) => w.id === workspaceId);
}

/**
 * Destructive workspace settings: slug-confirmed delete and artifact-repo Remove.
 *
 * @deployed
 */
test.describe("settings: danger-zone deletes (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test("@deployed workspace delete rejects wrong slug", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);

    await page.goto(`/settings/danger?ws=${encodeURIComponent(ws.id)}`);
    await expect(
      page.getByRole("heading", { name: "Danger zone", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.locator('input[name="slug_confirmation"]').fill("wrong-slug");
    await page.getByRole("button", { name: "Delete workspace" }).click();

    await expect(page).toHaveURL(/error=slug_mismatch/, { timeout: 20_000 });

    const get = await shipApiGet(
      request,
      `/v1/workspaces/${encodeURIComponent(ws.id)}`,
    );
    expect(get.ok(), `GET workspace → ${get.status()}`).toBeTruthy();
    expect(await workspaceListed(request, ws.id)).toBe(true);
  });

  test("@deployed artifact repo remove via registries UI", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    const repo = await createArtifactRepo(request, ws.id);

    await page.goto(`/settings/registries?ws=${encodeURIComponent(ws.id)}`);
    await expect(
      page.getByRole("heading", { name: "Registries", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(repo.url, { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    await page
      .locator("tr")
      .filter({ hasText: repo.url })
      .getByRole("button", { name: "Remove" })
      .click();

    await expect(page).toHaveURL(/\/settings/, { timeout: 20_000 });
    await page.reload();
    await expect(page.getByText(repo.url, { exact: true })).toHaveCount(0);

    const listed = await shipApiGet(
      request,
      `/v1/workspaces/${encodeURIComponent(ws.id)}/artifact-repos`,
    );
    expect(listed.ok(), `GET artifact-repos → ${listed.status()}`).toBeTruthy();
    const rows = (await listed.json()) as { id: string }[];
    expect(rows.some((r) => r.id === repo.id)).toBe(false);
  });

  test("@deployed workspace delete with correct slug", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);

    await page.goto(`/settings/danger?ws=${encodeURIComponent(ws.id)}`);
    await expect(
      page.getByRole("heading", { name: "Danger zone", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.locator('input[name="slug_confirmation"]').fill(ws.slug);
    await page.getByRole("button", { name: "Delete workspace" }).click();

    await expect(page).toHaveURL(/\/(?:\?|$)/, { timeout: 20_000 });
    expect(await workspaceListed(request, ws.id)).toBe(false);
  });
});
