import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Последовательная проверка основных экранов консоли Ship после онбординга.
 * Регистрацию не трогаем — нужен E2E_STORAGE_STATE.
 *
 * Порядок: операционный контур → очереди человека → каталог/знания → настройки.
 */
test.describe("console surfaces (wired, serial)", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
  });

  test("01 — dashboard loads", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Operating dashboard", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("02 — pipelines", async ({ page }) => {
    await page.goto("/pipelines");
    await expect(
      page.getByRole("heading", { name: "Pipelines", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(
        /All lanes Ship knows about|No pipelines yet\. Activate at least one repo/i,
      ),
    ).toBeVisible();
  });

  test("03 — clarifications inbox", async ({ page }) => {
    await page.goto("/clarifications");
    await expect(
      page.getByRole("heading", { name: "Clarifications", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("link", { name: /Open\s+\d+/ }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /Answered\s+\d+/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Skipped\s+\d+/ })).toBeVisible();
  });

  test("04 — improvements", async ({ page }) => {
    await page.goto("/improvements");
    await expect(
      page.getByRole("heading", { name: "Improvements", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("05 — artifact feedback", async ({ page }) => {
    await page.goto("/artifact-feedback");
    await expect(
      page.getByRole("heading", { name: "Artifact feedback", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/Complaints against catalog artifacts/i),
    ).toBeVisible();
  });

  test("06 — navigator (agent chat)", async ({ page }) => {
    await page.goto("/chat");
    await expect(
      page.getByRole("heading", { name: "Navigator", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page
        .getByText(/Agent not configured/i)
        .or(page.getByPlaceholder(/Ask the agent/)),
    ).toBeVisible();
  });

  test("07 — artifact catalog", async ({ page }) => {
    await page.goto("/catalog");
    await expect(
      page.getByRole("heading", { name: "Artifact catalog", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("08 — knowledge buckets", async ({ page }) => {
    await page.goto("/knowledge");
    await expect(
      page.getByRole("heading", { name: "Knowledge buckets", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("09 — metrics", async ({ page }) => {
    await page.goto("/metrics");
    await expect(
      page.getByRole("heading", { name: "Metrics", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("10 — workspace settings", async ({ page }) => {
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "Workspace settings", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("11 — integrations", async ({ page }) => {
    await page.goto("/integrations");
    await expect(
      page.getByRole("heading", { name: "Integrations", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("12 — members (team)", async ({ page }) => {
    await page.goto("/members");
    await expect(
      page.getByRole("heading", { name: "Members", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("13 — audit log", async ({ page }) => {
    await page.goto("/audit");
    await expect(
      page.getByRole("heading", { name: "Audit log", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("14 — rail: primary Operate links resolve", async ({ page }) => {
    await page.goto("/");
    const nav = page.locator("aside nav");
    const checks: [string, RegExp][] = [
      ["Pipelines", /\/pipelines$/],
      ["Clarifications", /\/clarifications$/],
      ["Improvements", /\/improvements$/],
      ["Feedback", /\/artifact-feedback$/],
      ["Navigator", /\/chat$/],
    ];
    for (const [label, pathRe] of checks) {
      await nav.getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(pathRe);
    }
    await nav.getByRole("link", { name: "Dashboard", exact: true }).click();
    await expect(page).toHaveURL({ pathname: "/" });
  });
});
