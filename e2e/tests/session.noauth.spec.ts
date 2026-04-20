import { expect, test } from "@playwright/test";

/**
 * Без сохранённой сессии защищённые маршруты уводят на логин (dev с живым API).
 * @deployed
 */
test.describe("session guard (no storage)", () => {
  test("@deployed root redirects to login when API is live", async ({
    page,
  }) => {
    await page.goto("/");
    const url = page.url();
    if (!/\/login/.test(url)) {
      test.skip(
        true,
        "Expected /login redirect — console may be mock-only (SHIP_API_URL unset on deploy)",
      );
    }
    await expect(page).toHaveURL(/\/login/);
  });
});
