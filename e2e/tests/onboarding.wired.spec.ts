import { expect, test, type Page } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";
import {
  buildWizardSeedResult,
  mockDonePageRoutes,
  mockWizardSeedLatest,
  seedWizardResultInSession,
} from "./_helpers/onboarding-mocks";

/**
 * Wave-8c onboarding wizard end-to-end coverage (P5-10).
 *
 * Steps in the wired flow: github → repos → tracker → CONFIRM → DONE.
 * The legacy ``configure`` and ``knowledge`` step ids 303-redirect to
 * ``confirm``; the per-repo preset radio is gone — every repo lands
 * on the canonical ``DEFAULT_BUNDLE``.
 *
 * Setup once per environment:
 *
 *   npx playwright codegen $E2E_CONSOLE_BASE_URL
 *   — sign in via Auth0, save storage to e2e/.auth/user.json (gitignored)
 *   export E2E_STORAGE_STATE=e2e/.auth/user.json
 *
 * The Confirm step and the Done step server-side fetch workspace +
 * repo state from the backend (App Router server components — those
 * fetches leave Next.js directly and are NOT interceptable via
 * :func:`Page.route`). The tests therefore skip gracefully when
 * neither ``E2E_STORAGE_STATE`` nor a backend with at least one
 * activated repo is reachable. Client-side endpoints
 * (``/api/onboard/wizard-seed-latest``, ``/api/onboard/intel-current``,
 * ``/api/onboard/intel-harvest``) ARE mocked via the helper in
 * ``_helpers/onboarding-mocks.ts`` so the polling badge / fallback
 * paths can be asserted deterministically.
 *
 * GitHub App install + OAuth are not stable in headless CI; keep
 * those manual or use a dedicated staging bot account + saved
 * storage in GitHub Actions secrets.
 */

const SEED_REPO_ID = "repo_seed_e2e_p5_10";

test.describe("onboarding wizard (authenticated)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE to a valid Playwright storageState JSON (see e2e/README.md)",
    );
  });

  // ────────────────────────────────────────────────────────────────
  // Existing GitHub-step coverage — kept verbatim from the pre-P5-10
  // spec because the install CTA and resume-step routing are still
  // live behaviour we want to regression-test.
  // ────────────────────────────────────────────────────────────────

  test("onboarding shows GitHub step or resumes further", async ({ page }) => {
    await page.goto("/onboarding?step=github");
    const githubHeading = page.getByRole("heading", {
      name: /install ship on github/i,
    });
    const reposHeading = page.getByRole("heading", {
      name: /which repos should ship watch/i,
    });
    const trackerHeading = page.getByRole("heading", {
      name: /connect your tracker/i,
    });
    const confirmContainer = page.getByTestId("onboarding-step-confirm");
    const doneContainer = page.getByTestId("onboarding-step-done");

    await expect(
      githubHeading
        .or(reposHeading)
        .or(trackerHeading)
        .or(confirmContainer)
        .or(doneContainer),
    ).toBeVisible({ timeout: 30_000 });
  });

  test("install CTA is present on github step when landing pinned to github", async ({
    page,
  }) => {
    await page.goto("/onboarding?step=github");
    const onGithubStep = await page
      .getByTestId("onboarding-install-github")
      .count()
      .then((n) => n > 0);
    if (!onGithubStep) {
      test.skip(true, "Resume skipped github step — already past install");
      return;
    }
    await expect(page.getByTestId("onboarding-install-github")).toBeVisible();
  });

  // ────────────────────────────────────────────────────────────────
  // P5-10 new coverage — Confirm + Done steps.
  // ────────────────────────────────────────────────────────────────

  test("renders confirm step with default bundle preview", async ({ page }) => {
    await page.goto("/onboarding?step=confirm");

    const confirm = page.getByTestId("onboarding-step-confirm");
    if (!(await isVisibleSoon(confirm))) {
      test.skip(
        true,
        "Backend / auth not wired: confirm step did not render. Tests rely on a workspace with ≥1 activated repo and SHIP_API_URL set on the console.",
      );
      return;
    }

    await expect(confirm).toBeVisible();

    const bundleItems = page.getByTestId("onboarding-confirm-bundle-item");
    await expect
      .poll(async () => bundleItems.count(), {
        message: "DEFAULT_BUNDLE preview should render at least 5 items",
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(5);

    // Each bundle item carries a strong title (top-line) and a
    // sibling reason paragraph immediately below. Sample the first
    // one rather than every entry — the catalog assertion lives in
    // the backend tests; here we only care the FE is wiring both.
    const firstItem = bundleItems.first();
    await expect(firstItem.locator("strong, .font-semibold").first()).toBeVisible();
    await expect(firstItem.locator("p").first()).toBeVisible();

    const repoCards = page.getByTestId("onboarding-confirm-repo-card");
    await expect(repoCards.first()).toBeVisible({ timeout: 10_000 });

    // CTA exists for at least the first card. May be disabled if
    // tracker / required secrets aren't bound — that's expected for
    // a fresh wizard run; we only assert presence here.
    const seedCta = page.getByTestId("onboarding-confirm-open-seed-pr").first();
    await expect(seedCta).toBeVisible();
  });

  test("done step shows pr link and routing summary when sessionStorage primed", async ({
    page,
  }) => {
    const repoId = await resolveDoneRepoId(page);
    if (repoId == null) {
      test.skip(
        true,
        "Backend not wired: workspace has no activated repos for the Done step to render.",
      );
      return;
    }

    const seeded = buildWizardSeedResult({
      pr_url: "https://github.com/acme/widgets/pull/1234",
      pr_number: 1234,
      branch: "ship/install-bootstrap-1",
      files: ["/.ship/config.yml", "/.github/workflows/pr-and-ci-gate.yml"],
      presets: ["default"],
      tracker_kind: null,
      synthetic_lanes_created: 7,
      codeowners: {
        file_found: true,
        rules_count: 3,
        routing_rules_created: 2,
        unresolved_owners: ["@ext"],
      },
      intel: { enqueued: true, job_id: "arq:job:abc", intel_id: null },
    });
    await seedWizardResultInSession(page, repoId, seeded);
    await mockDonePageRoutes(page, { intelCurrent: "missing" });

    const consoleErrors = collectConsoleErrors(page);

    await page.goto(`/onboarding?step=done&repo_id=${encodeURIComponent(repoId)}`);

    const done = page.getByTestId("onboarding-step-done");
    await expect(done).toBeVisible({ timeout: 15_000 });

    // The seeded card renders for the matched repo id; if the workspace
    // has additional repos they fall through to the API-fallback path
    // (mocked above to 404 → "no bootstrap yet"). We assert against the
    // seeded card by its data-repo-id.
    const card = page.locator(
      `[data-testid="onboarding-done-repo-card"][data-repo-id="${repoId}"]`,
    );
    await expect(card).toBeVisible({ timeout: 10_000 });

    const prLink = card.getByTestId("onboarding-done-pr-link");
    await expect(prLink).toBeVisible();
    await expect(prLink).toHaveAttribute("href", /pull\/1234/);

    const codeowners = card.getByTestId("onboarding-done-codeowners");
    await expect(codeowners).toBeVisible();
    await expect(codeowners).toContainText(/2\s+created/);
    await expect(codeowners).toContainText(/Unresolved/i);
    await expect(codeowners).toContainText("@ext");

    const intelBadge = card.getByTestId("onboarding-done-intel-badge");
    await expect(intelBadge).toBeVisible();
    // Either the inner harvesting state container is mounted (data-state
    // attribute) or the copy is rendered. Both are valid signals — the
    // FE uses the former for tests, the latter for users.
    await expect(intelBadge.locator('[data-state="harvesting"]')).toBeVisible();

    // MCP-first rework (ELS-290): the what's-next tile grid was
    // replaced by the attach-agent finale — same shared card as the
    // hub, plus the "I'll use the web console" skip link.
    const attach = page.getByTestId("onboarding-done-attach-agent");
    await expect(attach).toBeVisible();
    // Card may render collapsed if this storage state dismissed it on
    // the hub earlier — both shapes prove the component mounted.
    await expect(
      attach
        .getByTestId("connect-agent-card")
        .or(attach.getByTestId("connect-agent-hint")),
    ).toBeVisible();
    await expect(page.getByTestId("onboarding-done-skip")).toBeVisible();

    // Loose console-noise check: per-test we don't expect any console
    // errors from the seeded happy path. Hydration warnings would
    // surface here.
    expect(
      consoleErrors,
      `expected no console errors on done page, got: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("done step falls back to api when sessionStorage missing", async ({
    page,
  }) => {
    const repoId = await resolveDoneRepoId(page);
    if (repoId == null) {
      test.skip(
        true,
        "Backend not wired: workspace has no activated repos for the Done step to render.",
      );
      return;
    }

    const fixture = buildWizardSeedResult({
      pr_url: "https://github.com/acme/widgets/pull/9876",
      pr_number: 9876,
      branch: "ship/install-bootstrap-fallback",
      synthetic_lanes_created: 3,
      codeowners: {
        file_found: true,
        rules_count: 1,
        routing_rules_created: 1,
        unresolved_owners: [],
      },
      intel: { enqueued: false, job_id: null, intel_id: "intel_inline_001" },
    });
    // Note: NOT seeding sessionStorage — the FE must fall back to the API.
    await mockWizardSeedLatest(page, fixture);
    await mockDonePageRoutes(page, {
      seedLatest: fixture,
      intelCurrent: "ready",
    });

    const consoleErrors = collectConsoleErrors(page);

    await page.goto(`/onboarding?step=done&repo_id=${encodeURIComponent(repoId)}`);

    const done = page.getByTestId("onboarding-step-done");
    await expect(done).toBeVisible({ timeout: 15_000 });

    const card = page.locator(
      `[data-testid="onboarding-done-repo-card"][data-repo-id="${repoId}"]`,
    );
    await expect(card).toBeVisible({ timeout: 15_000 });

    const prLink = card.getByTestId("onboarding-done-pr-link");
    await expect(prLink).toBeVisible();
    await expect(prLink).toHaveAttribute("href", /pull\/9876/);

    const dataMissingErrors = consoleErrors.filter((line) =>
      /missing.*data|no.*wizard|undefined.*pr_url/i.test(line),
    );
    expect(
      dataMissingErrors,
      `expected no missing-data console errors, got: ${dataMissingErrors.join(" | ")}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Best-effort visibility probe with a short ceiling — used to decide skips. */
async function isVisibleSoon(
  locator: ReturnType<Page["getByTestId"]>,
  timeoutMs = 8_000,
): Promise<boolean> {
  try {
    await locator.first().waitFor({ state: "visible", timeout: timeoutMs });
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve a real activated-repo id for the Done step tests.
 *
 * Strategy:
 *   1. Land on the Confirm step (auto-resume should park us there
 *      whenever the workspace already owns at least one activated
 *      repo), read the first ``data-repo-id`` we find.
 *   2. If nothing renders within the probe window, return ``null``
 *      so the calling test can skip cleanly.
 *
 * Rationale: the Done step requires the SSR repo list to contain an
 * id that matches the seeded sessionStorage key (or the API
 * fallback). Hard-coding a synthetic id would render the empty
 * state unless ``filterRepos`` falls through, and even then the
 * card's ``data-repo-id`` would mismatch the seeded key. Reading
 * the live id keeps the test deterministic against any workspace
 * shape.
 */
async function resolveDoneRepoId(page: Page): Promise<string | null> {
  await page.goto("/onboarding?step=confirm");
  const card = page.getByTestId("onboarding-confirm-repo-card").first();
  if (!(await isVisibleSoon(card, 10_000))) {
    return null;
  }
  return await card.getAttribute("data-repo-id");
}

/**
 * Subscribe to ``console.error`` events (excluding noisy 3rd-party
 * warnings) and return the in-memory buffer the test can assert on.
 */
function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (/Download the React DevTools/i.test(text)) return;
    if (/Hydration failed/i.test(text)) {
      // Surface hydration errors — they're real regressions for SSR'd
      // components. Don't filter them out.
    }
    errors.push(text);
  });
  return errors;
}
