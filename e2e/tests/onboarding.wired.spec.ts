import { expect, test } from "@playwright/test";

import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Full onboarding UI checks — requires a real console + backend + session.
 *
 * Setup once per environment:
 *   npx playwright codegen $E2E_CONSOLE_BASE_URL
 *   — sign in via Auth0, save storage to e2e/.auth/user.json (gitignored)
 *   export E2E_STORAGE_STATE=e2e/.auth/user.json
 *
 * GitHub App install + OAuth are not stable in headless CI; keep those manual
 * or use a dedicated staging bot account + saved storage in GitHub Actions secrets.
 */
test.describe("onboarding wizard (authenticated)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE to a valid Playwright storageState JSON (see e2e/README.md)",
    );
  });

  test("onboarding shows GitHub step or resumes further", async ({ page }) => {
    await page.goto("/onboarding?step=github");
    // Either step 1 (install) or auto-resume jumped ahead — both are success.
    const githubHeading = page.getByRole("heading", {
      name: /install ship on github/i,
    });
    const reposHeading = page.getByRole("heading", {
      name: /which repos should ship watch/i,
    });
    const trackerHeading = page.getByRole("heading", {
      name: /pick a tracker/i,
    });
    const doneHeading = page.getByRole("heading", {
      name: /you're wired in/i,
    });

    await expect(
      githubHeading.or(reposHeading).or(trackerHeading).or(doneHeading),
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
});
