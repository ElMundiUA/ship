/**
 * Phase 7b smoke — connector-proxy bucket surfaces.
 *
 * Locks the console → Distiller round-trip for a connector bucket
 * end-to-end:
 *   1. Seed a throwaway Integration row via PUT so we have a real
 *      UUID to reference (the bucket-create route rejects unknown
 *      integration ids).
 *   2. Open ``/knowledge`` and confirm the new-bucket dialog renders
 *      both the Upload and Connector tabs.
 *   3. Create the bucket directly via the API (the dialog itself is
 *      covered by unit tests; this test's focus is the detail page).
 *   4. On ``/knowledge/<slug>`` the ConnectorCard should render with
 *      the provider + resource_ref, and clicking "Sync now" should
 *      pop a ``new`` result banner.
 *   5. A second sync should resolve to ``skip`` via content_sha
 *      dedupe so the "already up to date" state is covered too.
 *
 * Requires:
 *   - E2E_STORAGE_STATE — signed-in console session.
 *   - E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN — a Ship PAT with
 *     ``workspace:write`` so we can mint the Integration + bucket.
 */

import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiBase,
  shipApiPost,
  shipApiToken,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

async function upsertWebhookIntegration(
  request: import("@playwright/test").APIRequestContext,
  workspaceId: string,
): Promise<string> {
  const base = shipApiBase()!;
  const token = shipApiToken()!;
  const resp = await request.fetch(
    `${base}/v1/workspaces/${workspaceId}/integrations/webhook`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      data: JSON.stringify({
        kind: "webhook",
        config: { note: "phase7b-connector-smoke" },
        secret: null,
      }),
    },
  );
  expect(resp.status(), "PUT /integrations/webhook").toBeLessThan(300);
  const body = (await resp.json()) as { id: string };
  return body.id;
}

test.describe("knowledge connector bucket (wired)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  test("detail page renders connector card and syncs an article", async ({
    page,
    request,
  }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    const integrationId = await upsertWebhookIntegration(request, workspaceId);

    const slug = `e2e-connector-${Date.now().toString(36)}`;
    const bucket = await shipApiPost(
      request,
      `/v1/workspaces/${workspaceId}/buckets`,
      {
        slug,
        name: "E2E connector",
        source_kind: "connector_proxy",
        source_ref: {
          integration_id: integrationId,
          resource_ref: { database_id: "phase7b-smoke" },
        },
      },
    );
    expect(
      bucket.status(),
      "POST /buckets connector should 200/201",
    ).toBeLessThan(300);

    await page.goto("/knowledge");
    await page.getByTestId("new-bucket-open").click();
    await expect(page.getByTestId("new-bucket-dialog")).toBeVisible();
    await expect(page.getByTestId("new-bucket-kind-connector")).toBeVisible();
    await page.getByTestId("new-bucket-close").click();

    await page.goto(`/knowledge/${encodeURIComponent(slug)}`);
    const card = page.getByTestId("bucket-connector-card");
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card).toContainText(/webhook/i);
    await expect(card).toContainText("phase7b-smoke");

    await page.getByTestId("bucket-connector-sync").click();
    const result = page.getByTestId("bucket-connector-sync-result");
    await expect(result).toBeVisible({ timeout: 30_000 });
    await expect(result).toHaveAttribute("data-ok", "true");
    await expect(result).toContainText(/new|created/i);

    // Second click -> skip (content_sha dedupe).
    await page.getByTestId("bucket-connector-sync").click();
    await expect(result).toContainText(/skip|already up to date/i, {
      timeout: 30_000,
    });

    await expect(page.getByTestId("bucket-articles")).toBeVisible();
  });
});
