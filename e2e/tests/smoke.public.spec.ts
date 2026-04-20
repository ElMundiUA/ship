import { expect, test } from "@playwright/test";

/**
 * No login — safe in CI without secrets.
 * Proves the console serves the login surface and linked flows render.
 */
test.describe("public console shell", () => {
  test("login page exposes sign-in entry", async ({ page }) => {
    await page.goto("/login");
    await expect(
      page.getByRole("heading", { name: /sign in to keep/i }),
    ).toBeVisible();
  });

  test("login page links to legal terms", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("link", { name: /^terms$/i })).toBeVisible();
  });
});
