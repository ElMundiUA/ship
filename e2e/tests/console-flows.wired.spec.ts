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

  test("01 — workspace home loads", async ({ page }) => {
    // Phase-1 two-mode shell: `/` is the workspace home, not the
    // per-repo "Operating dashboard" (that moved under
    // `/r/<owner>/<repo>` and reaches full content in PR-4).
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Workspace home", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("heading", { name: "Repos", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Fleet", exact: true }),
    ).toBeVisible();
  });

  test("03 — inbox", async ({ page }) => {
    await page.goto("/inbox");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
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

  test("08 — knowledge buckets", async ({ page }) => {
    await page.goto("/knowledge");
    await expect(
      page.getByRole("heading", { name: "Knowledge buckets", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    // Phase 4: the scope pill is mounted in the header and defaults
    // to "workspace" scope when no ?scope= query param is set.
    const pill = page.getByTestId("scope-pill");
    await expect(pill).toBeVisible({ timeout: 10_000 });
    await expect(pill).toHaveAttribute("data-scope", "workspace");
  });

  test("08c — scope pill is mounted on inbox/chat (Phase 4b)", async ({
    page,
  }) => {
    // One pill test per surface so regressions flag which page lost
    // the pill rather than one big sweep. All four are the same
    // assertion shape — default scope is workspace when no query
    // param is set.
    const surfaces = [
      "/inbox",
      "/chat",
    ] as const;
    for (const path of surfaces) {
      await page.goto(path);
      const pill = page.getByTestId("scope-pill");
      await expect(pill, `pill on ${path}`).toBeVisible({ timeout: 30_000 });
      await expect(
        pill,
        `default scope on ${path}`,
      ).toHaveAttribute("data-scope", "workspace");
    }
  });

  test("08b — knowledge respects ?scope=repo URL state", async ({ page }) => {
    // Navigate to a repo-scoped URL directly. The pill should flip
    // to "repo" iff the ``repo_id`` resolves against activated
    // repos — otherwise it silently falls back to "workspace" so
    // the empty state doesn't hijack the page. We can't hard-code a
    // real repo id in a public fixture, so we assert the fallback
    // path is at least not an error (no crash on unknown repo_id).
    await page.goto("/knowledge?scope=repo&repo_id=00000000-0000-0000-0000-000000000000");
    await expect(
      page.getByRole("heading", { name: "Knowledge buckets", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    const pill = page.getByTestId("scope-pill");
    await expect(pill).toBeVisible({ timeout: 10_000 });
    // Unknown repo id → pill stays on workspace (graceful fallback).
    await expect(pill).toHaveAttribute("data-scope", "workspace");
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

  test("14 — rail: workspace nav exposes active workspace pages", async ({
    page,
  }) => {
    await page.goto("/");
    const nav = page.locator("aside nav");
    const checks: [string, RegExp][] = [
      ["Process", /\/process$/],
      ["Reports", /\/reports$/],
      ["Knowledge", /\/knowledge$/],
      ["Audit log", /\/audit$/],
    ];
    await expect(
      nav.getByRole("link", { name: /^Inbox · \d+$/ }),
    ).toBeVisible();
    await nav.getByRole("link", { name: /^Inbox · \d+$/ }).click();
    await expect(page).toHaveURL(/\/inbox$/);
    for (const [label, pathRe] of checks) {
      await nav.getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(pathRe);
    }
    await nav.getByRole("link", { name: "Home", exact: true }).click();
    await expect(page).toHaveURL(/\/(?:$|\?)/);
  });

  test("14b — header Navigator launcher opens /chat", async ({ page }) => {
    await page.goto("/");
    const launcher = page.getByTestId("navigator-launcher");
    await expect(launcher).toBeVisible({ timeout: 15_000 });
    await launcher.click();
    await expect(page).toHaveURL(/\/chat(?:\?|$)/);
  });

  test("15 — repo mode: click a repo card → /r/<owner>/<repo>", async ({
    page,
  }) => {
    // Phase-1 two-mode shell: the workspace home lists activated
    // repos as channels. Clicking one must land on
    // `/r/<owner>/<repo>` and swap the sidebar to the repo nav.
    // We pick the first repo tile that the backend fed in.
    await page.goto("/");
    const firstRepoLink = page.getByTestId("repo-channel").first();
    const ok = await firstRepoLink
      .waitFor({ state: "visible", timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    if (!ok) {
      test.info().annotations.push({
        type: "skip",
        description:
          "No activated repos in this workspace; repo-mode flow can't be exercised.",
      });
      return;
    }
    await firstRepoLink.click();
    await expect(page).toHaveURL(/\/r\/[^/]+\/[^/?#]+(?:\?|$)/, {
      timeout: 15_000,
    });
    // Repo-mode sidebar shows repo-scoped operate items (Lanes,
    // Requests). The workspace-only "Fleet requests" entry must
    // NOT appear on the same rail.
    const nav = page.locator("aside nav");
    await expect(
      nav.getByRole("link", { name: "Lanes", exact: true }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      nav.getByRole("link", { name: "Requests", exact: true }),
    ).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "Fleet requests", exact: true }),
    ).toHaveCount(0);
  });
});
