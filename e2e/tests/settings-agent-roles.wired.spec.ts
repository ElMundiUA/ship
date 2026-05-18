import { expect, test, type Page } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiDelete,
  shipApiGet,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type AgentRoleRow = {
  slug: string;
  name: string;
  prompt: string;
  base_role_slug?: string | null;
};

type AgentRoleList = { roles: AgentRoleRow[] };

type ResolveOut = { source: string; prompt: string };

const skipReason =
  "E2E_STORAGE_STATE + E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)";

const DEFAULT_SLUG = "developer";
const E2E_PREFIX = "e2e-ar-";

function uniqueSlug(suffix: string): string {
  return `${E2E_PREFIX}${suffix}-${Date.now().toString(36)}`;
}

function wsAgentRolesPath(workspaceId: string): string {
  return `/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles`;
}

async function listWorkspaceRoles(
  request: APIRequestContext,
  workspaceId: string,
): Promise<AgentRoleRow[]> {
  const res = await shipApiGet(request, wsAgentRolesPath(workspaceId));
  expect(res.ok(), `GET agent-roles → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as AgentRoleList;
  return body.roles ?? [];
}

async function deleteWorkspaceRole(
  request: APIRequestContext,
  workspaceId: string,
  slug: string,
): Promise<void> {
  const res = await shipApiDelete(
    request,
    `${wsAgentRolesPath(workspaceId)}/${encodeURIComponent(slug)}`,
  );
  if (res.status() === 204 || res.status() === 404) return;
  expect(res.status(), `DELETE ${slug} → ${res.status()}`).toBe(204);
}

async function cleanupE2eAgentRoles(
  request: APIRequestContext,
  workspaceId: string,
): Promise<void> {
  const roles = await listWorkspaceRoles(request, workspaceId);
  for (const row of roles) {
    if (row.slug.startsWith(E2E_PREFIX)) {
      await deleteWorkspaceRole(request, workspaceId, row.slug);
    }
  }
}

async function getWorkspaceRole(
  request: APIRequestContext,
  workspaceId: string,
  slug: string,
): Promise<AgentRoleRow | null> {
  const res = await shipApiGet(
    request,
    `${wsAgentRolesPath(workspaceId)}/${encodeURIComponent(slug)}`,
  );
  if (res.status() === 404) return null;
  expect(res.ok(), `GET role ${slug} → ${res.status()}`).toBeTruthy();
  return (await res.json()) as AgentRoleRow;
}

async function getResolve(
  request: APIRequestContext,
  workspaceId: string,
  slug: string,
): Promise<ResolveOut> {
  const res = await shipApiGet(
    request,
    `${wsAgentRolesPath(workspaceId)}/${encodeURIComponent(slug)}/resolve`,
  );
  expect(res.ok(), `GET resolve ${slug} → ${res.status()}`).toBeTruthy();
  return (await res.json()) as ResolveOut;
}

async function getShipDefaultPrompt(
  request: APIRequestContext,
  slug: string,
): Promise<string> {
  const res = await shipApiGet(
    request,
    `/v1/agent-roles/${encodeURIComponent(slug)}`,
  );
  expect(res.ok(), `GET ship default ${slug} → ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as { prompt: string };
  return body.prompt;
}

async function gotoAgentRoles(
  page: Page,
  workspaceId: string,
): Promise<void> {
  await page.goto(
    `/settings/agent-roles?ws=${encodeURIComponent(workspaceId)}`,
  );
  await expect(
    page.getByRole("heading", { name: "Agent roles", exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("heading", { name: /Workspace overrides/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Ship defaults" }),
  ).toBeVisible();
}

function shipDefaultRow(page: Page, slug: string) {
  const section = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Ship defaults" }),
  });
  return section.locator("div.flex.items-center").filter({
    has: page.locator("code", { hasText: slug }),
  }).first();
}

function customsSection(page: Page) {
  return page.locator("section").filter({
    has: page.getByRole("heading", { name: /Workspace overrides/ }),
  });
}

function customsRow(page: Page, slug: string) {
  return customsSection(page).locator("div.flex.items-center").filter({
    has: page.locator("code", { hasText: slug }),
  }).first();
}

async function saveEditor(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save", exact: true }),
  ).toHaveCount(0, { timeout: 20_000 });
}

/**
 * Agent roles settings: clone, override, delete-revert, duplicate slug.
 *
 * @deployed
 */
test.describe("settings: agent roles (wired, serial)", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  let workspaceId = "";

  test.beforeEach(async ({ request }) => {
    test.skip(
      !hasPlaywrightStorageState() || !hasShipApiCredentials(),
      skipReason,
    );
    workspaceId = await shipResolveWorkspaceId(request);
    await cleanupE2eAgentRoles(request, workspaceId);
    await deleteWorkspaceRole(request, workspaceId, DEFAULT_SLUG);
  });

  test.afterEach(async ({ request }) => {
    if (!hasShipApiCredentials()) return;
    await cleanupE2eAgentRoles(request, workspaceId);
    await deleteWorkspaceRole(request, workspaceId, DEFAULT_SLUG);
  });

  test("@deployed clone → edit → reload persists via API", async ({
    page,
    request,
  }) => {
    const cloneSlug = uniqueSlug("clone");
    const marker = `e2e-clone-marker-${Date.now().toString(36)}`;

    await gotoAgentRoles(page, workspaceId);

    const devRow = shipDefaultRow(page, DEFAULT_SLUG);
    await devRow.getByRole("button", { name: "Clone as new" }).click();
    await expect(
      page.getByRole("heading", { name: `Clone "${DEFAULT_SLUG}"` }),
    ).toBeVisible();

    await page.locator('input[placeholder="developer-mobile"]').fill(cloneSlug);
    await saveEditor(page);

    await expect(customsSection(page).getByText(cloneSlug)).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      customsSection(page).getByText(`Cloned from ${DEFAULT_SLUG}`),
    ).toBeVisible();

    await customsRow(page, cloneSlug).getByRole("button", { name: "Edit" }).click();
    await expect(
      page.getByRole("heading", { name: `Edit "${cloneSlug}"` }),
    ).toBeVisible();
    await page.locator("textarea").fill(marker);
    await saveEditor(page);

    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Agent roles", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(customsSection(page).getByText(cloneSlug)).toBeVisible();
    await expect(
      customsSection(page).getByText(`Cloned from ${DEFAULT_SLUG}`),
    ).toBeVisible();

    const row = await getWorkspaceRole(request, workspaceId, cloneSlug);
    expect(row?.prompt).toBe(marker);
    expect(row?.base_role_slug).toBe(DEFAULT_SLUG);
  });

  test("@deployed override mutes default and resolve uses workspace", async ({
    page,
    request,
  }) => {
    await deleteWorkspaceRole(request, workspaceId, DEFAULT_SLUG);
    const marker = `e2e-override-${Date.now().toString(36)}`;

    await gotoAgentRoles(page, workspaceId);

    const devRow = shipDefaultRow(page, DEFAULT_SLUG);
    await expect(devRow).not.toHaveClass(/opacity-60/);
    await devRow.getByRole("button", { name: "Override" }).click();
    await expect(
      page.getByRole("heading", {
        name: `Override Ship default "${DEFAULT_SLUG}"`,
      }),
    ).toBeVisible();
    await page.locator("textarea").fill(marker);
    await saveEditor(page);

    const customs = customsSection(page);
    await expect(customs.getByText(DEFAULT_SLUG)).toBeVisible({
      timeout: 15_000,
    });
    await expect(customs.getByText("Override", { exact: true })).toBeVisible();

    const mutedRow = shipDefaultRow(page, DEFAULT_SLUG);
    await expect(mutedRow).toHaveClass(/opacity-60/);
    await expect(mutedRow.getByText("Overridden in workspace")).toBeVisible();
    await expect(
      mutedRow.getByRole("button", { name: "Override" }),
    ).toHaveCount(0);
    await expect(
      mutedRow.getByRole("button", { name: "Edit override" }),
    ).toBeVisible();

    const resolved = await getResolve(request, workspaceId, DEFAULT_SLUG);
    expect(resolved.source).toBe("workspace");
    expect(resolved.prompt).toBe(marker);
  });

  test("@deployed delete override reverts to ship default", async ({
    page,
    request,
  }) => {
    const shipPrompt = await getShipDefaultPrompt(request, DEFAULT_SLUG);

    await shipApiPost(request, wsAgentRolesPath(workspaceId), {
      slug: DEFAULT_SLUG,
      name: "Developer (e2e override)",
      prompt: `e2e-delete-seed-${Date.now().toString(36)}`,
    });

    await gotoAgentRoles(page, workspaceId);

    const customs = customsSection(page);
    await expect(customs.getByText(DEFAULT_SLUG)).toBeVisible({
      timeout: 15_000,
    });

    page.once("dialog", (dialog) => dialog.accept());
    await customsRow(page, DEFAULT_SLUG)
      .getByRole("button", { name: "Delete" })
      .click();

    await expect(customs.getByText(DEFAULT_SLUG)).toHaveCount(0, {
      timeout: 20_000,
    });

    const devRow = shipDefaultRow(page, DEFAULT_SLUG);
    await expect(devRow).not.toHaveClass(/opacity-60/);
    await expect(devRow.getByText("Overridden in workspace")).toHaveCount(0);
    await expect(
      devRow.getByRole("button", { name: "Override" }),
    ).toBeVisible();

    const resolved = await getResolve(request, workspaceId, DEFAULT_SLUG);
    expect(resolved.source).toBe("ship_default");
    expect(resolved.prompt).toBe(shipPrompt);
  });

  test("@deployed duplicate clone slug shows 409 in error banner", async ({
    page,
    request,
  }) => {
    const dupSlug = uniqueSlug("dup");
    const create = await shipApiPost(request, wsAgentRolesPath(workspaceId), {
      slug: dupSlug,
      name: "E2E dup seed",
      prompt: "seed for conflict test",
      base_role_slug: DEFAULT_SLUG,
    });
    expect(create.status(), `seed POST → ${create.status()}`).toBeLessThan(
      300,
    );

    const beforeCount = (await listWorkspaceRoles(request, workspaceId)).length;

    await gotoAgentRoles(page, workspaceId);

    const devRow = shipDefaultRow(page, DEFAULT_SLUG);
    await devRow.getByRole("button", { name: "Clone as new" }).click();
    await page.locator('input[placeholder="developer-mobile"]').fill(dupSlug);
    await page.getByRole("button", { name: "Save", exact: true }).click();

    const banner = page.locator(".text-coral").first();
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText(
      `workspace already has an agent role with slug '${dupSlug}'`,
    );

    const afterCount = (await listWorkspaceRoles(request, workspaceId)).length;
    expect(afterCount).toBe(beforeCount);
    await expect(
      customsSection(page).locator("code", { hasText: dupSlug }),
    ).toHaveCount(1);
  });
});
