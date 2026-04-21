/**
 * Phase 7 smoke — external-static upload surface on the bucket detail
 * page.
 *
 * This locks the console → Distiller → article round-trip through the
 * real UI:
 *   1. Seed a fresh external-static bucket via the Ship API
 *      (so the test doesn't depend on pre-existing fixture data).
 *   2. Open `/knowledge/<slug>` in the console.
 *   3. Confirm the upload card is mounted.
 *   4. Drop a small .md file + force `classifier=stub` for
 *      deterministic output.
 *   5. Assert the inline result shows `new` and — after the server
 *      revalidation — the articles table lists the new article.
 *
 * Requires:
 *   - E2E_STORAGE_STATE — signed-in console session.
 *   - E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN — a Ship PAT with
 *     ``workspace:write`` so we can mint the seed bucket.
 */

import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

test.describe("knowledge upload (wired)", () => {
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

  test("upload card ingests a markdown file", async ({ page, request }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    // Unique slug per run — the backend upserts on
    // (workspace, scope, slug) so repeated runs don't collide.
    const slug = `e2e-upload-${Date.now().toString(36)}`;
    const name = "E2E upload target";

    const create = await shipApiPost(
      request,
      `/v1/workspaces/${workspaceId}/buckets`,
      { slug, name, description: "Phase 7 E2E bucket" },
    );
    expect(
      create.status(),
      "POST /buckets should 200 or 201 for a fresh slug",
    ).toBeLessThan(300);

    await page.goto(`/knowledge/${encodeURIComponent(slug)}`);

    // The bucket may newly-create without a legacy repo_files row; the
    // page still mounts with the unified bucket card stack. We wait
    // on the upload card's testid because it's the affordance we're
    // actually validating.
    const uploadCard = page.getByTestId("bucket-upload-card");
    await expect(uploadCard).toBeVisible({ timeout: 30_000 });

    // Pin to the deterministic classifier so the article decision is
    // independent of whether the test cluster has an OPENAI_API_KEY.
    await page.getByTestId("bucket-upload-classifier").selectOption("stub");

    const payload = Buffer.from(
      [
        `# ${name}`,
        "",
        "This is a Phase 7 E2E upload. It should land as a new article.",
        `stamp: ${Date.now()}`,
      ].join("\n"),
      "utf-8",
    );
    await page.getByTestId("bucket-upload-input").setInputFiles({
      name: "phase7-smoke.md",
      mimeType: "text/markdown",
      buffer: payload,
    });

    await page.getByTestId("bucket-upload-submit").click();

    // The server action writes to the backend, then ``revalidatePath``
    // refreshes the server components. The inline result banner flips
    // synchronously; the articles card re-renders after the reload.
    const result = page.getByTestId("bucket-upload-result");
    await expect(result).toBeVisible({ timeout: 30_000 });
    await expect(result).toHaveAttribute("data-ok", "true");
    await expect(result).toContainText(/new|created/i);

    // Articles table should now be rendered (either fresh from the
    // revalidated fetch, or already populated by a previous retry).
    await expect(page.getByTestId("bucket-articles")).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page.getByText("phase7-smoke.md", { exact: false }),
    ).toBeVisible();
  });

  test("upload card rejects an oversize file client-side", async ({
    page,
    request,
  }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    const slug = `e2e-upload-big-${Date.now().toString(36)}`;
    const create = await shipApiPost(
      request,
      `/v1/workspaces/${workspaceId}/buckets`,
      { slug, name: "E2E oversize" },
    );
    expect(create.status()).toBeLessThan(300);

    await page.goto(`/knowledge/${encodeURIComponent(slug)}`);
    await expect(page.getByTestId("bucket-upload-card")).toBeVisible({
      timeout: 30_000,
    });

    // 1.5 MiB — over the 1 MiB cap both client + server enforce.
    const oversize = Buffer.alloc(1_500_000, 0x61);
    await page.getByTestId("bucket-upload-input").setInputFiles({
      name: "too-big.md",
      mimeType: "text/markdown",
      buffer: oversize,
    });
    await page.getByTestId("bucket-upload-submit").click();

    const result = page.getByTestId("bucket-upload-result");
    await expect(result).toBeVisible({ timeout: 10_000 });
    await expect(result).toHaveAttribute("data-ok", "false");
    await expect(result).toContainText(/too large/i);
  });
});
