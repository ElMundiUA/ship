import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiDelete,
  shipApiGet,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiRepo = { id: string };
type ApiRepoSecret = {
  id: string;
  name: string;
  masked_hint: string | null;
  sync_status: string;
  updated_at: string;
};

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

function uniqueSecretName(): string {
  const ts = Date.now().toString(36).toUpperCase();
  return `E2E_SHIP_${ts}`;
}

async function firstActivatedRepo(
  request: APIRequestContext,
  workspaceId: string,
): Promise<ApiRepo | null> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos`,
  );
  expect(res.ok(), `GET /repos → ${res.status()}`).toBeTruthy();
  const repos = (await res.json()) as ApiRepo[];
  return repos[0] ?? null;
}

async function listRepoSecrets(
  request: APIRequestContext,
  workspaceId: string,
  repoId: string,
): Promise<ApiRepoSecret[]> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets`,
  );
  if (!res.ok()) return [];
  const body = (await res.json()) as { items?: ApiRepoSecret[] };
  return body.items ?? [];
}

async function purgeSecret(
  request: APIRequestContext,
  workspaceId: string,
  repoId: string,
  secretName: string,
  secretId: string | null,
): Promise<void> {
  const pathFor = (id: string) =>
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/secrets/${encodeURIComponent(id)}`;

  if (secretId) {
    const del = await shipApiDelete(request, pathFor(secretId));
    if (del.ok()) return;
  }

  const rows = await listRepoSecrets(request, workspaceId, repoId);
  const row = rows.find((s) => s.name === secretName.toUpperCase());
  if (row) {
    await shipApiDelete(request, pathFor(row.id));
  }
}

/**
 * Repo secrets page — add, rotate, and delete via native HTML forms.
 *
 * @deployed
 */
test.describe("repo secrets: add, rotate, delete (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  let workspaceId = "";
  let repoId = "";
  let secretName = "";
  let secretId: string | null = null;

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
  });

  test.afterEach(async ({ request }) => {
    if (!workspaceId || !repoId || !secretName) return;
    await purgeSecret(request, workspaceId, repoId, secretName, secretId);
    secretId = null;
  });

  test("@deployed add, rotate, and delete via native forms", async ({
    page,
    request,
  }) => {
    workspaceId = await shipResolveWorkspaceId(request);
    const repo = await firstActivatedRepo(request, workspaceId);
    if (!repo) {
      test.skip(true, "no activated repo in workspace — skip repo-secrets flow");
      return;
    }

    repoId = repo.id;
    secretName = uniqueSecretName();
    const valueV1 = `e2e-v1-${Date.now()}`;
    const valueV2 = `e2e-v2-${Date.now()}`;

    const secretsUrl = `/repos/${encodeURIComponent(repoId)}/secrets`;

    await page.goto(secretsUrl);
    await expect(
      page.getByRole("heading", {
        name: "Ship-managed Actions secrets",
        exact: true,
      }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Stored secrets", { exact: false })).toBeVisible();

    await page.locator('input[name="name"]').fill(secretName);
    await page.locator('textarea[name="value"]').fill(valueV1);
    await page.getByRole("button", { name: "Save secret" }).click();

    await expect(page).toHaveURL(/banner=ok.*reason=created|reason=created.*banner=ok/, {
      timeout: 60_000,
    });
    await expect(page.getByText("Secret saved and synced to GitHub.")).toBeVisible();

    const storedName = secretName.toUpperCase();
    const row = page.locator("tbody tr").filter({ hasText: storedName });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText(/^(synced|pending)$/)).toBeVisible();

    const updatedBefore = (await row.locator("td").nth(5).textContent())?.trim() ?? "";
    const hintBefore =
      (await row.locator("td").nth(1).textContent())?.trim() ?? "";

    const listedAfterCreate = await listRepoSecrets(request, workspaceId, repoId);
    const created = listedAfterCreate.find((s) => s.name === storedName);
    expect(created, "secret row visible in API after create").toBeTruthy();
    secretId = created?.id ?? null;

    await page.locator('input[name="name"]').fill(secretName);
    await page.locator('textarea[name="value"]').fill(valueV2);
    await page.getByRole("button", { name: "Save secret" }).click();

    await expect(page).toHaveURL(/banner=ok.*reason=rotated|reason=rotated.*banner=ok/, {
      timeout: 60_000,
    });
    await expect(
      page.getByText("Secret rotated — GitHub now has the new value."),
    ).toBeVisible();

    const rowAfterRotate = page.locator("tbody tr").filter({ hasText: storedName });
    await expect(rowAfterRotate).toBeVisible({ timeout: 15_000 });
    const updatedAfter =
      (await rowAfterRotate.locator("td").nth(5).textContent())?.trim() ?? "";
    const hintAfter =
      (await rowAfterRotate.locator("td").nth(1).textContent())?.trim() ?? "";
    expect(
      updatedAfter !== updatedBefore || hintAfter !== hintBefore,
      "rotate should change updated_at or masked hint",
    ).toBeTruthy();

    await rowAfterRotate.getByRole("button", { name: "Delete" }).click();

    await expect(page).toHaveURL(/banner=ok.*reason=deleted|reason=deleted.*banner=ok/, {
      timeout: 60_000,
    });
    await expect(
      page.getByText("Secret deleted from Ship and GitHub."),
    ).toBeVisible();
    await expect(page.locator("tbody tr").filter({ hasText: storedName })).toHaveCount(0);

    secretId = null;
    const listedAfterDelete = await listRepoSecrets(request, workspaceId, repoId);
    expect(
      listedAfterDelete.some((s) => s.name === storedName),
      "secret absent from API after UI delete",
    ).toBe(false);
  });
});
