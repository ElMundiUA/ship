/**
 * Workspace registries settings — empty state, UI create, and invalid URL validation.
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

const INVALID_URL_PATTERN =
  /That URL doesn.t look right\. Use file:\/\/, https:\/\/, ssh:\/\/, or git@host:path style\./;

function uniqueSlug(): string {
  return `e2e-reg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function registriesPath(workspaceId: string): string {
  return `/settings/registries?ws=${encodeURIComponent(workspaceId)}`;
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

async function listArtifactRepos(
  request: Parameters<typeof shipApiGet>[0],
  workspaceId: string,
): Promise<ApiArtifactRepo[]> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/artifact-repos`,
  );
  expect(res.ok(), `GET artifact-repos → ${res.status()}`).toBeTruthy();
  return (await res.json()) as ApiArtifactRepo[];
}

async function deleteArtifactRepo(
  request: Parameters<typeof shipApiDelete>[0],
  workspaceId: string,
  repoId: string,
): Promise<void> {
  const res = await shipApiDelete(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/artifact-repos/${encodeURIComponent(repoId)}`,
  );
  expect(
    res.ok() || res.status() === 204,
    `DELETE artifact-repo → ${res.status()}`,
  ).toBeTruthy();
}

async function deleteThrowawayWorkspace(
  request: Parameters<typeof shipApiDelete>[0],
  workspaceId: string,
): Promise<void> {
  const res = await shipApiDelete(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}`,
  );
  expect(
    res.ok() || res.status() === 204,
    `DELETE workspace → ${res.status()}`,
  ).toBeTruthy();
}

async function expandAddRepoForm(page: Page): Promise<void> {
  await page.getByText("+ Add repo", { exact: true }).click();
}

async function submitAddRepoForm(page: Page, url: string): Promise<void> {
  await expandAddRepoForm(page);
  await page.locator('input[name="url"]').fill(url);
  await page.getByRole("button", { name: "Register" }).click();
}

test.describe("settings: registries (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test("@deployed empty state — heading, copy, and add control", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    try {
      await page.goto(registriesPath(ws.id));
      await expect(
        page.getByRole("heading", { name: "Registries", exact: true }),
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByText("No artifact repos registered yet", { exact: false }),
      ).toBeVisible();
      await expect(page.getByText("+ Add repo", { exact: true })).toBeVisible();
    } finally {
      await deleteThrowawayWorkspace(request, ws.id);
    }
  });

  test("@deployed create repo via UI — table row and API list", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);
    const repoUrl = `file:///tmp/e2e-registries-${Date.now()}`;
    let createdId: string | null = null;

    try {
      await page.goto(registriesPath(ws.id));
      await expect(
        page.getByRole("heading", { name: "Registries", exact: true }),
      ).toBeVisible({ timeout: 30_000 });

      await submitAddRepoForm(page, repoUrl);
      await expect(page).toHaveURL(/\/settings(?:\?|$)/, { timeout: 20_000 });

      await page.goto(registriesPath(ws.id));
      await expect(page.getByText(repoUrl, { exact: true })).toBeVisible({
        timeout: 15_000,
      });

      const apiRows = await listArtifactRepos(request, ws.id);
      const created = apiRows.find((r) => r.url === repoUrl);
      expect(created).toBeTruthy();
      createdId = created!.id;
    } finally {
      if (createdId) {
        await deleteArtifactRepo(request, ws.id, createdId);
      }
      await deleteThrowawayWorkspace(request, ws.id);
    }
  });

  test("@deployed invalid URL — settings error banner", async ({
    page,
    request,
  }) => {
    const ws = await createThrowawayWorkspace(request);

    try {
      await page.goto(registriesPath(ws.id));
      await expect(
        page.getByRole("heading", { name: "Registries", exact: true }),
      ).toBeVisible({ timeout: 30_000 });

      await submitAddRepoForm(page, "not-a-url");
      await expect(page).toHaveURL(/error=invalid_url/, { timeout: 20_000 });
      await expect(page.getByText(INVALID_URL_PATTERN)).toBeVisible({
        timeout: 15_000,
      });

      const apiRows = await listArtifactRepos(request, ws.id);
      expect(apiRows.some((r) => r.url === "not-a-url")).toBe(false);
    } finally {
      await deleteThrowawayWorkspace(request, ws.id);
    }
  });
});
