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

  test("01 — residual status surface loads (ELS-239)", async ({ page }) => {
    // `/` is the headless-pivot residual surface: engine health +
    // autonomy/console-surface values + deep links out. The rich
    // WorkspaceHome (Repos/Fleet) was deleted with its backend render
    // routes in Phase 4. Multi-workspace operators land on the entry
    // picker first — pin via ?ws when the env provides a workspace id
    // (local stacks seed several).
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    await expect(
      page.getByText("Headless status", { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("heading", { name: "Engine", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/Where the work lives/i).first(),
    ).toBeVisible();
  });

  test("03 — inbox", async ({ page }) => {
    await page.goto("/inbox");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("inbox-lane-filters")).toBeVisible();
  });

  test("03b — inbox lane chip filters without navigation", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByTestId("inbox-lane-filters")).toBeVisible({
      timeout: 30_000,
    });
    const rows = page.getByTestId("inbox-mailbox-rows").locator("li");
    const total = await rows.count();
    if (total === 0) {
      test.info().annotations.push({
        type: "skip",
        description: "No actionable inbox rows in wired workspace.",
      });
      return;
    }
    const urlBefore = page.url();
    await page.getByTestId("inbox-lane-now").click();
    await expect(page).toHaveURL(urlBefore);
    const visible = await rows.count();
    for (let i = 0; i < visible; i++) {
      await expect(rows.nth(i)).toHaveAttribute("data-lane", "now");
    }
    if (total > visible) {
      expect(visible).toBeLessThan(total);
    }
  });

  // 03c (reports surface) retired: /reports was deleted on main in
  // 7848f89d (ELS-165/166, Inbox Decision UI Phase 3) — reports now
  // land as inbox rows. Never caught because the wired suite is not
  // in CI and 01's failure serial-aborted the rest of this block.

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
      page.getByRole("heading", { name: "Knowledge", exact: true }),
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
    // the pill rather than one big sweep. The inbox dropped the pill
    // with the ELS-146 mailbox redesign — /chat (and /knowledge via
    // test 08) are the remaining pill surfaces.
    const surfaces = [
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
      page.getByRole("heading", { name: "Knowledge", exact: true }),
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

  test("11 — integrations (redirects into settings tab)", async ({ page }) => {
    // Sprint B collapsed /integrations into the settings mega-page —
    // next.config issues a permanent redirect that keeps the query.
    await page.goto("/integrations");
    await expect(page).toHaveURL(/\/settings\?.*tab=integrations/);
    await expect(
      page.getByRole("heading", { name: "Workspace settings", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("12 — members (redirects into settings tab)", async ({ page }) => {
    await page.goto("/members");
    await expect(page).toHaveURL(/\/settings\?.*tab=members/);
    await expect(
      page.getByRole("heading", { name: "Workspace settings", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("13 — audit page retired (ELS-237): route falls through to 404/redirect", async ({ page }) => {
    const resp = await page.goto("/audit");
    // The render page is deleted; either Next 404s or the console-mode
    // gate redirects to the status landing. Both are acceptable — what
    // must NOT happen is an Audit log heading rendering.
    await expect(
      page.getByRole("heading", { name: "Audit log", exact: true }),
    ).toHaveCount(0);
    if (resp) expect([200, 404]).toContain(resp.status());
  });

  test("14 — rail: workspace nav exposes active workspace pages", async ({
    page,
  }) => {
    // Same multi-workspace pinning as test 01 — bare "/" lands on the
    // entry picker when the operator has several workspaces.
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    const nav = page.locator("aside nav");
    const checks: [string, RegExp][] = [
      ["Process", /\/process(\?|$)/],
      ["Knowledge", /\/knowledge(\?|$)/],
      ["Policies", /\/settings\/policy(\?|$)/],
    ];
    await expect(
      nav.getByRole("link", { name: /^Inbox(?: · \d+)?$/ }),
    ).toBeVisible();
    await nav.getByRole("link", { name: /^Inbox(?: · \d+)?$/ }).click();
    await expect(page).toHaveURL(/\/inbox(\?|$)/);
    for (const [label, pathRe] of checks) {
      await nav.getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(pathRe);
    }
    await nav.getByRole("link", { name: "Dashboard", exact: true }).click();
    await expect(page).toHaveURL(/\/(?:$|\?)/);
  });

  test("14b — header Navigator launcher opens /chat", async ({ page }) => {
    // The residual "/" surface renders AppShellChrome without the
    // header bar (ELS-239), so the launcher lives on the header'd
    // pages — assert it from the Inbox, the always-on approval
    // surface.
    await page.goto("/inbox");
    const launcher = page.getByTestId("navigator-launcher");
    await expect(launcher).toBeVisible({ timeout: 15_000 });
    await launcher.click();
    await expect(page).toHaveURL(/\/chat(?:\?|$)/);
  });

  // 15 (repo mode /r/<owner>/<repo>) retired with Phase 4: the repo
  // dashboard + its repo-channel tiles were deleted (ELS-238/239) —
  // repo state now lives in GitHub, the residual surface only deep-
  // links out. test_strangler_regression_gate.py pins the backend
  // half (repo_home render route gone).
});
