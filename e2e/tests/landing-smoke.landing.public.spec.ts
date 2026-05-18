import { expect, test } from "@playwright/test";

/**
 * Marketing site smoke — no Auth0, sandbox, or console secrets.
 * Requires `apps/landing` built and `next start` at LANDING_BASE_URL.
 */
test.describe("landing smoke", () => {
  test("home page shows hero heading", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: /clear cockpit/i }),
    ).toBeVisible();
  });

  test("blog index shows main heading", async ({ page }) => {
    const response = await page.goto("/blog");
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: /run Ship in public/i }),
    ).toBeVisible();
  });

  test("header Blog nav reaches blog index", async ({ page }) => {
    await page.goto("/");
    await page
      .locator("header nav")
      .getByRole("link", { name: "Blog", exact: true })
      .click();
    await expect(page).toHaveURL(/\/blog\/?$/);
    await expect(
      page.getByRole("heading", { level: 1, name: /run Ship in public/i }),
    ).toBeVisible();
  });
});
