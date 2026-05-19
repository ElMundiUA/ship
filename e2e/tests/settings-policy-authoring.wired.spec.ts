/**
 * Workspace policy authoring — create, toggle persistence, delete,
 * client validation, and role-scoping on /settings/policy.
 *
 * @deployed
 */
import { expect, test, type Page } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiDelete,
  shipApiGet,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiPolicy = {
  id: string;
  title: string;
  body: string;
  enabled: boolean;
  sort_order: number;
  applies_to_roles: string[] | null;
};

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

function uniqueTitle(): string {
  return `e2e-policy-${Date.now()}`;
}

function wsQuery(workspaceId: string): string {
  return `?ws=${encodeURIComponent(workspaceId)}`;
}

async function listPoliciesApi(
  request: Parameters<typeof shipApiGet>[0],
  workspaceId: string,
): Promise<ApiPolicy[]> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies`,
  );
  expect(res.ok(), `GET policies → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as { policies: ApiPolicy[] };
  return body.policies;
}

async function deletePolicyApi(
  request: Parameters<typeof shipApiDelete>[0],
  workspaceId: string,
  policyId: string,
): Promise<void> {
  const res = await shipApiDelete(
    request,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/${encodeURIComponent(policyId)}`,
  );
  expect(
    res.ok() || res.status() === 204,
    `DELETE policy → ${res.status()}`,
  ).toBeTruthy();
}

function policyRow(page: Page, title: string) {
  return page
    .getByRole("heading", { name: title, level: 3 })
    .locator("..")
    .locator("..")
    .locator("..");
}

async function fillNewPolicyForm(
  page: Page,
  opts: {
    title: string;
    body: string;
    sortOrder?: number;
    roleSlugs?: string[];
  },
): Promise<void> {
  await page.getByLabel("Title").fill(opts.title);
  await page.getByLabel("Body (markdown)").fill(opts.body);
  if (opts.sortOrder !== undefined) {
    await page.getByLabel("Sort order").fill(String(opts.sortOrder));
  }
  if (opts.roleSlugs?.includes("developer")) {
    await page.getByRole("button", { name: "Developer", exact: true }).click();
  }
}

test.describe("settings: policy authoring (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  const createdIds: string[] = [];
  let workspaceId = "";
  let wsSuffix = "";

  test.beforeEach(async ({ request }) => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
    workspaceId = await shipResolveWorkspaceId(request);
    wsSuffix = wsQuery(workspaceId);
  });

  test.afterEach(async ({ request }) => {
    if (!workspaceId || createdIds.length === 0) return;
    for (const id of [...createdIds]) {
      try {
        await deletePolicyApi(request, workspaceId, id);
      } catch {
        // Best-effort cleanup for shared e2e workspace.
      }
    }
    createdIds.length = 0;
  });

  test("@deployed validation — empty title shows inline error", async ({
    page,
  }) => {
    await page.goto(`/settings/policy/new${wsSuffix}`);
    await expect(
      page.getByRole("heading", { name: "New policy", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("Body (markdown)").fill("Body without a title.");
    await page.locator("form").evaluate((f) => f.setAttribute("novalidate", ""));
    await page.getByRole("button", { name: "Create policy" }).click();

    await expect(page.getByText("Title is required.")).toBeVisible();
    await expect(page).toHaveURL(/\/settings\/policy\/new/);
  });

  test("@deployed validation — empty body shows inline error", async ({
    page,
  }) => {
    await page.goto(`/settings/policy/new${wsSuffix}`);
    await expect(
      page.getByRole("heading", { name: "New policy", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    await page.getByLabel("Title").fill("Title without body");
    await page.getByLabel("Body (markdown)").fill("   ");
    await page.locator("form").evaluate((f) => f.setAttribute("novalidate", ""));
    await page.getByRole("button", { name: "Create policy" }).click();

    await expect(page.getByText("Body is required.")).toBeVisible();
    await expect(page).toHaveURL(/\/settings\/policy\/new/);
  });

  test("@deployed create scoped policy — list + API", async ({
    page,
    request,
  }) => {
    const title = uniqueTitle();
    const body = `Standing rule body ${title}`;
    const sortOrder = 42;

    await page.goto(`/settings/policy/new${wsSuffix}`);
    await fillNewPolicyForm(page, {
      title,
      body,
      sortOrder,
      roleSlugs: ["developer"],
    });
    await page.getByRole("button", { name: "Create policy" }).click();

    await expect(page).toHaveURL(/\/settings\/policy(?:\?|$)/, {
      timeout: 30_000,
    });
    await expect(
      page.getByRole("heading", { name: "Policies", exact: true }),
    ).toBeVisible();

    const row = policyRow(page, title);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row.getByText("Developer", { exact: true })).toBeVisible();
    await expect(row.getByText(`sort ${sortOrder}`)).toBeVisible();
    await expect(row.getByRole("switch")).toHaveAttribute(
      "aria-checked",
      "true",
    );

    const apiRows = await listPoliciesApi(request, workspaceId);
    const created = apiRows.find((p) => p.title === title);
    expect(created).toBeTruthy();
    expect(created!.applies_to_roles).toEqual(["developer"]);
    expect(created!.enabled).toBe(true);
    createdIds.push(created!.id);

    const preamble = await shipApiGet(
      request,
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/preamble?role=developer`,
    );
    expect(preamble.ok(), `GET preamble → ${preamble.status()}`).toBeTruthy();
    const preambleBody = (await preamble.json()) as { preamble: string | null };
    expect(preambleBody.preamble).toContain(body);
  });

  test("@deployed toggle persists after reload", async ({ page, request }) => {
    const title = uniqueTitle();
    const body = `Toggle persistence ${title}`;

    await page.goto(`/settings/policy/new${wsSuffix}`);
    await fillNewPolicyForm(page, { title, body, roleSlugs: ["developer"] });
    await page.getByRole("button", { name: "Create policy" }).click();
    await expect(page).toHaveURL(/\/settings\/policy/, { timeout: 30_000 });

    const apiAfterCreate = await listPoliciesApi(request, workspaceId);
    const created = apiAfterCreate.find((p) => p.title === title);
    expect(created?.enabled).toBe(true);
    createdIds.push(created!.id);

    const row = policyRow(page, title);
    await row.getByRole("switch", { name: "Disable policy" }).click();
    await expect(row.getByRole("switch")).toHaveAttribute(
      "aria-checked",
      "false",
      { timeout: 15_000 },
    );

    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Policies", exact: true }),
    ).toBeVisible({ timeout: 30_000 });

    const rowAfterReload = policyRow(page, title);
    await expect(rowAfterReload.getByRole("switch")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    await expect(rowAfterReload.locator("h3")).toHaveClass(/line-through/);

    const apiAfterToggle = await listPoliciesApi(request, workspaceId);
    const toggled = apiAfterToggle.find((p) => p.id === created!.id);
    expect(toggled?.enabled).toBe(false);
  });

  test("@deployed delete removes row after reload", async ({
    page,
    request,
  }) => {
    const title = uniqueTitle();
    const body = `Delete me ${title}`;

    await page.goto(`/settings/policy/new${wsSuffix}`);
    await fillNewPolicyForm(page, { title, body, roleSlugs: ["developer"] });
    await page.getByRole("button", { name: "Create policy" }).click();
    await expect(page).toHaveURL(/\/settings\/policy/, { timeout: 30_000 });

    const apiAfterCreate = await listPoliciesApi(request, workspaceId);
    const created = apiAfterCreate.find((p) => p.title === title);
    expect(created).toBeTruthy();
    const policyId = created!.id;

    const row = policyRow(page, title);
    page.once("dialog", (d) => d.accept());
    await row.getByRole("button", { name: "Delete policy" }).click();

    await expect(row).toBeHidden({ timeout: 15_000 });

    await page.reload();
    await expect(page.locator("h3").filter({ hasText: title })).toHaveCount(0);

    const apiAfterDelete = await listPoliciesApi(request, workspaceId);
    expect(apiAfterDelete.some((p) => p.id === policyId)).toBe(false);
  });

  test("@deployed delete confirm dismissed keeps row", async ({
    page,
    request,
  }) => {
    const title = uniqueTitle();
    const body = `Keep me ${title}`;

    await page.goto(`/settings/policy/new${wsSuffix}`);
    await fillNewPolicyForm(page, { title, body });
    await page.getByRole("button", { name: "Create policy" }).click();
    await expect(page).toHaveURL(/\/settings\/policy/, { timeout: 30_000 });

    const apiAfterCreate = await listPoliciesApi(request, workspaceId);
    const created = apiAfterCreate.find((p) => p.title === title);
    expect(created).toBeTruthy();
    createdIds.push(created!.id);

    const row = policyRow(page, title);
    page.once("dialog", (d) => d.dismiss());
    await row.getByRole("button", { name: "Delete policy" }).click();

    await expect(row).toBeVisible();
    const apiStillThere = await listPoliciesApi(request, workspaceId);
    expect(apiStillThere.some((p) => p.id === created!.id)).toBe(true);
  });
});
