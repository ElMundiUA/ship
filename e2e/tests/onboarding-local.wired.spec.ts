/**
 * Onboarding smoke + entry-redirect coverage (laptop profile).
 *
 * The deployed-Auth0 suite in ``onboarding.wired.spec.ts`` covers
 * step-content and helper mocks against a saved Auth0 storageState.
 * This spec runs against the laptop-offline profile (``make dev-up``
 * + memory adapters) and uses the local-auth login flow rather
 * than Auth0, so a fresh laptop dev can exercise onboarding-entry
 * flows without setting up Auth0 credentials.
 *
 * Coverage:
 *
 *   O1 — Authed user with a not-yet-wired workspace hitting any
 *        in-app route redirects through to ``/onboarding?step=...``.
 *   O2 — ``/onboarding`` lands on a step screen (github / tracker
 *        / confirm) and renders a "next step" affordance.
 *   O4 — ``/onboarding`` from a logged-out browser bounces to
 *        ``/login?reason=...``.
 *
 * Gate: ``E2E_LOCAL_AUTH=true`` + ``E2E_SHIP_API_BASE`` pointing at
 * the laptop backend. The dev seed user (``dev@ship.dev``) is
 * provisioned by ``seed_dev.py`` with a fresh "dev" workspace.
 */

import { expect, test } from "@playwright/test";

import {
  buildLocalStorageState,
  localAuthEnabled,
} from "../lib/local-auth";


test.describe("onboarding — laptop profile", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(
      !localAuthEnabled(),
      "Set E2E_LOCAL_AUTH=true to run the laptop-profile onboarding spec",
    );
  });

  test("O1 — /onboarding renders for an authed dev user", async ({
    playwright,
    browser,
  }) => {
    // Log in against the local backend, mint the ship_session cookie,
    // open a context with that storageState. We avoid the saved-file
    // path so the spec works on first invocation without any setup
    // beyond ``make dev-up`` + a seed run.
    const apiCtx = await playwright.request.newContext();
    const state = await buildLocalStorageState(apiCtx);
    await apiCtx.dispose();

    const ctx = await browser.newContext({ storageState: state });
    const page = await ctx.newPage();
    // /onboarding is reachable directly when authed. The dev user
    // has a seeded workspace but no wired tracker / repo /
    // workflows, so the wizard renders a step screen rather than
    // bouncing back to the dashboard.
    await page.goto("/onboarding");
    await expect(page).toHaveURL(/\/onboarding(\?|$)/);
    // Some step (github / tracker / confirm) renders. We don't
    // pin on the exact heading copy — wizard re-flows often —
    // only that the page mounted and shows step chrome.
    const ok = await Promise.race([
      page
        .getByRole("heading", { name: /onboarding|connect|wire|set up/i })
        .first()
        .waitFor({ timeout: 30_000 })
        .then(() => true)
        .catch(() => false),
      page
        .getByText(/step\s*\d/i)
        .first()
        .waitFor({ timeout: 30_000 })
        .then(() => true)
        .catch(() => false),
    ]);
    expect(ok, "onboarding page renders some step chrome").toBe(true);
    await ctx.close();
  });

  test("O2 — onboarding renders a step screen with a next-step affordance", async ({
    playwright,
    browser,
  }) => {
    const apiCtx = await playwright.request.newContext();
    const state = await buildLocalStorageState(apiCtx);
    await apiCtx.dispose();

    const ctx = await browser.newContext({ storageState: state });
    const page = await ctx.newPage();
    await page.goto("/onboarding");
    // Any step screen should render at least one CTA that lets the
    // operator move forward — install GitHub App, pick tracker,
    // confirm bundle, etc.  We don't pin on the specific copy
    // because the wizard re-flows often; we just assert *some*
    // forward-motion affordance is present so a regression that
    // drops every CTA fails this test instead of silently leaving
    // dev stuck on step 1.
    const forwardCta = page
      .getByRole("link", { name: /(install|continue|next|connect|confirm)/i })
      .or(page.getByRole("button", { name: /(install|continue|next|connect|confirm)/i }));
    await expect(forwardCta.first()).toBeVisible({ timeout: 30_000 });
    await ctx.close();
  });

  test("O4 — logged-out browser bounces to /login", async ({ browser }) => {
    // Explicitly empty storageState: @playwright/test merges the
    // project-level `use.storageState` into bare browser.newContext()
    // calls, so when E2E_STORAGE_STATE is set globally a "fresh"
    // context would silently arrive authenticated and render the
    // wizard instead of bouncing.
    const ctx = await browser.newContext({
      storageState: { cookies: [], origins: [] },
    });
    const page = await ctx.newPage();
    await page.goto("/onboarding");
    // Auth middleware kicks in before the page renders; final URL
    // should be /login (with some reason query). We accept the
    // login URL with or without a query string so the test isn't
    // brittle to the exact reason code.
    await expect(page).toHaveURL(/\/login(\?|$)/, { timeout: 30_000 });
    await ctx.close();
  });
});
