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

  test("01 — operator hub loads (ELS-287)", async ({ page }) => {
    // `/` is the MCP-first operator hub: engine health, the
    // Connect-your-agent card, the "Waiting on you" strip, and deep
    // links out. Multi-workspace operators land on the entry picker
    // first — pin via ?ws when the env provides a workspace id.
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    await expect(
      page.getByText("Operator hub", { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("heading", { name: "Engine", exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByTestId("connect-agent-card")
        .or(page.getByTestId("connect-agent-hint")),
    ).toBeVisible();
    await expect(page.getByTestId("waiting-on-you")).toBeVisible();
    await expect(
      page.getByText(/Where the work lives/i).first(),
    ).toBeVisible();
  });

  test("01b — hub fits 320px with no horizontal scroll", async ({ page }) => {
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.setViewportSize({ width: 320, height: 720 });
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    await expect(
      page.getByText("Operator hub", { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 });
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow, "horizontal scroll at 320px").toBeLessThanOrEqual(0);
  });

  test("03 — /inbox is gone (mailbox removed, ELS-289)", async ({ page }) => {
    // The mailbox page was deleted in the MCP-first rework — inbox
    // triage lives in the operator agent (MCP) and Telegram. The
    // route must 404 (full mode) or 302 to the hub (gated modes);
    // what must NOT happen is an Inbox mailbox rendering.
    const resp = await page.goto("/inbox");
    await expect(
      page.getByRole("heading", { name: "Inbox", exact: true }),
    ).toHaveCount(0);
    if (resp) expect([200, 404]).toContain(resp.status());
  });

  test("03b — /approve/{id} confirm page renders the missing-item state", async ({
    page,
  }) => {
    // The per-item confirm surface (deep-link target for Telegram
    // buttons + MCP web_url refusals) must render gracefully for an
    // unknown id — no crash, explicit "gone" copy, hub link back.
    await page.goto("/approve/00000000-0000-0000-0000-000000000000");
    await expect(
      page.getByRole("heading", { name: "Waiting on you", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("approve-missing")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Back to the hub/i }),
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
    await expect(page).toHaveURL(/\/settings(\/|\?)/);
    await expect(
      page.getByRole("heading", { name: "Workspace settings", exact: true }),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("12 — members (redirects into settings tab)", async ({ page }) => {
    await page.goto("/members");
    await expect(page).toHaveURL(/\/settings(\/|\?)/);
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

  test("14 — rail is exactly Chat / Settings (ELS-289)", async ({
    page,
  }) => {
    // Same multi-workspace pinning as test 01 — bare "/" lands on the
    // entry picker when the operator has several workspaces.
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/?ws=${encodeURIComponent(ws)}` : "/");
    const nav = page.locator("aside nav");
    // Exactly two entries; Inbox/Process/Knowledge/Policies left the
    // rail in the MCP-first rework (still routable — Settings →
    // Advanced surfaces covers wayfinding).
    await expect(nav.getByRole("link")).toHaveCount(2, { timeout: 30_000 });
    await expect(
      nav.getByRole("link", { name: /Inbox|Process|Knowledge|Policies|Dashboard/ }),
    ).toHaveCount(0);
    await nav.getByRole("link", { name: "Chat", exact: true }).click();
    await expect(page).toHaveURL(/\/chat(\?|$)/);
    await page.locator("aside nav").getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings(\?|$)/);
  });

  test("14b — header Navigator launcher opens /chat", async ({ page }) => {
    // The hub "/" renders AppShellChrome without the header bar, so
    // the launcher lives on the header'd pages — assert it from
    // Settings.
    await page.goto("/settings");
    const launcher = page.getByTestId("navigator-launcher");
    await expect(launcher).toBeVisible({ timeout: 15_000 });
    await launcher.click();
    await expect(page).toHaveURL(/\/chat(?:\?|$)/);
  });

  test("14c — Settings → Advanced surfaces links the de-railed pages", async ({
    page,
  }) => {
    // Pin ?ws like tests 01/14 — a bare /settings on a multi-workspace
    // account races the workspace-cookie resolution.
    const ws = process.env.E2E_WORKSPACE_ID?.trim();
    await page.goto(ws ? `/settings?ws=${encodeURIComponent(ws)}` : "/settings");
    const advanced = page.getByTestId("advanced-surfaces");
    await expect(advanced).toBeVisible({ timeout: 30_000 });
    await advanced.getByRole("link", { name: /Process editor/ }).click();
    await expect(page).toHaveURL(/\/process(\?|$)/);
  });

  // 15 (repo mode /r/<owner>/<repo>) retired with Phase 4: the repo
  // dashboard + its repo-channel tiles were deleted (ELS-238/239) —
  // repo state now lives in GitHub, the residual surface only deep-
  // links out. test_strangler_regression_gate.py pins the backend
  // half (repo_home render route gone).
});
