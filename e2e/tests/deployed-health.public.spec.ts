import { expect, test } from "@playwright/test";

/**
 * Базовая готовность **задеплоенного dev**: консоль отдаётся, API живой.
 * @deployed
 */
test.describe("deployed dev — smoke", () => {
  test("@deployed console login page responds", async ({ page, request }) => {
    const res = await page.goto("/login");
    expect(res?.ok()).toBeTruthy();
    await expect(
      page.getByRole("heading", { name: /sign in to keep/i }),
    ).toBeVisible({ timeout: 20_000 });
  });

  test("@deployed Ship API /v1/health", async ({ request }) => {
    const base = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
    test.skip(!base, "Set E2E_SHIP_API_BASE for API health check");
    const res = await request.get(`${base}/v1/health`, {
      headers: { Accept: "application/json" },
    });
    expect(res.ok(), `/v1/health → ${res.status()}`).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.database).toMatch(/ok|^error:/);
  });
});
