import { expect, test } from "@playwright/test";

/**
 * Marketing site smoke — no Auth0, sandbox, or console secrets.
 * Requires `apps/landing` built and `next start` at LANDING_BASE_URL.
 */
test.describe("landing smoke", () => {
  test("home page shows founder hero heading", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: /describe it/i }),
    ).toBeVisible();
  });

  test("home page shows narrative sections in order", async ({ page }) => {
    await page.goto("/");

    const describe = page.getByRole("heading", { level: 2, name: /^Describe$/ });
    const build = page.getByRole("heading", { level: 2, name: /^Ship Builds It$/ });
    const goLive = page.getByRole("heading", { level: 2, name: /^Go Live$/ });

    await expect(describe).toBeVisible();
    await expect(build).toBeVisible();
    await expect(goLive).toBeVisible();

    const describeBox = await describe.boundingBox();
    const buildBox = await build.boundingBox();
    const goLiveBox = await goLive.boundingBox();
    expect(describeBox).not.toBeNull();
    expect(buildBox).not.toBeNull();
    expect(goLiveBox).not.toBeNull();
    expect(describeBox!.y).toBeLessThan(buildBox!.y);
    expect(buildBox!.y).toBeLessThan(goLiveBox!.y);
  });

  test("home page shows waitlist email capture", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#waitlist")).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /join the waitlist/i }),
    ).toBeVisible();
  });

  test("homepage waitlist submits successfully when API returns ok", async ({ page }) => {
    await page.route("**/api/waitlist", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.goto("/");
    await page.getByLabel("Email").fill("founder@example.com");
    await page.getByRole("button", { name: /join the waitlist/i }).click();
    await expect(page.getByText(/thanks — you're on the list/i)).toBeVisible();
  });

  test("homepage waitlist shows error when API fails", async ({ page }) => {
    await page.route("**/api/waitlist", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "Server error" }),
      });
    });

    await page.goto("/");
    await page.getByLabel("Email").fill("founder@example.com");
    await page.getByRole("button", { name: /join the waitlist/i }).click();
    await expect(page.getByText(/server error/i)).toBeVisible();
  });

  test("hero demo respects reduced motion — no animate-ping", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.locator(".animate-ping")).toHaveCount(0);
  });

  test("home page has no horizontal overflow at 320px", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto("/");
    const overflow = await page.evaluate(
      () => document.body.scrollWidth > window.innerWidth,
    );
    expect(overflow).toBe(false);
  });

  test("blog index shows main heading", async ({ page }) => {
    const response = await page.goto("/blog");
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: /run Ship in public/i }),
    ).toBeVisible();
  });

  test("beta page still shows beta heading", async ({ page }) => {
    const response = await page.goto("/beta");
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /request access to the ship closed beta/i,
      }),
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
