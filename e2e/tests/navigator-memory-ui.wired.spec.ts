/**
 * Navigator memory — Console UI (E17/M11-M13).
 *
 * Drives the ``/memory`` page in a real authenticated browser session
 * (``E2E_STORAGE_STATE`` — the operator's saved Auth0 cookies). The
 * page is a Server Component for the read + two Client islands for
 * the destructive actions; the islands fetch the Next.js shim routes
 * ``/api/memory/delete`` and ``/api/memory/forget`` which fan out to
 * the Ship API server-side.
 *
 * Coverage:
 *   M11 — page renders with the "Only you can see this page" kicker;
 *          either the empty state or the per-project sections appear
 *   M12 — row-level "Forget" → arm → "Confirm" deletes the fact and
 *          the page reflects the removal after ``router.refresh()``
 *   M13 — bulk "Forget the last N days" form rejects without the
 *          consent checkbox and applies when checked
 *
 * Seeding for M12/M13 reuses the sandbox endpoint with the operator's
 * existing PAT (``E2E_SHIP_API_TOKEN``) — Denys is a member of the
 * e2e-navigator workspace per ``setup_e2e_navigator_workspace.py``,
 * so the seed lands under his ``owner_user_id`` and is visible to
 * the same identity in the browser session.
 */

import { expect, test } from "@playwright/test";

import {
  cleanAllMemories,
  hasMemorySuiteCredentials,
  listMemories,
  memorySuiteEnv,
  seedMemory,
  type AuthCtx,
} from "../lib/memory-helpers";
import { hasPlaywrightStorageState } from "../lib/storage";


/** Operator's own PAT — the one that auth resolves to Denys. */
function operatorCtxOrSkip(): AuthCtx | null {
  const env = memorySuiteEnv();
  const operatorPat = process.env.E2E_SHIP_API_TOKEN?.trim();
  if (!env.base || !env.workspaceId || !operatorPat) return null;
  return { base: env.base, token: operatorPat, workspaceId: env.workspaceId };
}


test.describe("navigator memory — Console /memory page", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE — the saved operator Auth0 session",
    );
    test.skip(
      !hasMemorySuiteCredentials(),
      "Set E2E_NAVIGATOR_WORKSPACE_ID + E2E_SHIP_API_BASE",
    );
  });

  // -------------------------------------------------------------------------
  // M11 — page renders
  // -------------------------------------------------------------------------

  test("M11 /memory renders with personal-scope kicker", async ({ page }) => {
    const env = memorySuiteEnv();
    await page.goto(`/memory?ws=${env.workspaceId}`);
    await expect(
      page.getByRole("heading", { name: "Memory", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText("Only you can see this page.", { exact: false }),
    ).toBeVisible();
    // Either the empty-state or a per-project section MUST be visible.
    const empty = page.getByText(/No memories yet/i);
    const heading = page.getByRole("heading", {
      name: /General|E2E/i,
    });
    await expect(empty.or(heading.first())).toBeVisible({ timeout: 15_000 });
  });

  // -------------------------------------------------------------------------
  // M12 — row delete arm/confirm
  // -------------------------------------------------------------------------

  test("M12 row-level Forget arms then deletes on Confirm", async ({
    page,
    request,
  }) => {
    const ctx = operatorCtxOrSkip();
    test.skip(
      ctx === null,
      "Seeding for M12 needs E2E_SHIP_API_TOKEN (Denys's PAT)",
    );
    await cleanAllMemories(request, ctx!);
    const marker = `m12-${Date.now()}`;
    const seeded = await seedMemory(request, ctx!, `One-off ${marker} fact.`);
    if (seeded.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    expect(seeded.id).toBeTruthy();

    await page.goto(`/memory?ws=${ctx!.workspaceId}`);
    const row = page
      .locator("li")
      .filter({ hasText: marker })
      .first();
    await expect(row, "seeded row visible").toBeVisible({ timeout: 15_000 });

    const forgetBtn = row.getByRole("button", { name: /Forget/i });
    await forgetBtn.click();
    // Arm-confirm — same button, the label flips to "Confirm".
    const confirmBtn = row.getByRole("button", { name: /Confirm/i });
    await expect(confirmBtn).toBeVisible({ timeout: 5_000 });
    await confirmBtn.click();

    // The Server Component refetches via ``router.refresh()``; the
    // row should disappear within a few seconds.
    await expect(row).toHaveCount(0, { timeout: 10_000 });

    // Backend should agree.
    const list = await listMemories(request, ctx!, { limit: 50 });
    expect(list.items.some((r) => r.id === seeded.id)).toBe(false);
  });

  // -------------------------------------------------------------------------
  // M13 — bulk-forget form
  // -------------------------------------------------------------------------

  test("M13 bulk-forget refuses without the consent checkbox", async ({
    page,
    request,
  }) => {
    const ctx = operatorCtxOrSkip();
    test.skip(ctx === null, "Seeding for M13 needs E2E_SHIP_API_TOKEN");
    await cleanAllMemories(request, ctx!);
    const seeded = await seedMemory(request, ctx!, "bulk-target");
    if (seeded.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    await page.goto(`/memory?ws=${ctx!.workspaceId}`);

    const forgetSubmit = page.getByRole("button", { name: /^Forget$/ });
    await expect(forgetSubmit, "Forget button mounted").toBeVisible({
      timeout: 15_000,
    });
    await expect(forgetSubmit, "disabled without consent").toBeDisabled();

    const consent = page.getByLabel(/I understand this is permanent/i);
    await consent.check();
    await expect(forgetSubmit, "enabled with consent").toBeEnabled();

    await forgetSubmit.click();
    // The bulk endpoint may fire fast — wait for the per-row UI to
    // empty out + the backend to agree.
    await expect(
      page.getByText(/No memories yet|bulk-target/i),
    ).toBeVisible({ timeout: 15_000 });
    // Final state — bulk-target row should be gone.
    await expect(
      page.locator("li").filter({ hasText: "bulk-target" }),
    ).toHaveCount(0, { timeout: 10_000 });
    const list = await listMemories(request, ctx!, { limit: 50 });
    expect(list.items.some((r) => r.fact_text === "bulk-target")).toBe(false);
  });
});
