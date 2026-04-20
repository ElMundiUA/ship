import type { Page } from "@playwright/test";

/**
 * Completes the GitHub App installation wizard on github.com after the
 * console has redirected here from POST /api/onboard/github-install.
 *
 * GitHub changes markup often — keep selectors defensive. Requires the
 * browser context to already be logged into GitHub (cookies in storageState)
 * or E2E_GITHUB_USERNAME / E2E_GITHUB_PASSWORD for a 2FA-disabled bot.
 *
 * Env:
 *   E2E_GITHUB_INSTALL_ACCOUNT — label on the account/org card (optional)
 *   E2E_GITHUB_REPO_FULL_NAME   — "owner/name" to select when picking repos
 *   E2E_GITHUB_USERNAME         — login (only if GitHub session missing)
 *   E2E_GITHUB_PASSWORD         — login (only if 2FA is off)
 */

async function dismissCookieBanner(page: Page) {
  const reject = page.getByRole("button", { name: /^(Reject|Dismiss)/i });
  if (await reject.isVisible().catch(() => false)) {
    await reject.click().catch(() => {});
  }
}

async function ensureGitHubLogin(page: Page) {
  const user = process.env.E2E_GITHUB_USERNAME;
  const pass = process.env.E2E_GITHUB_PASSWORD;
  const passwordField = page.getByLabel(/Password/i).first();
  if (!(await passwordField.isVisible().catch(() => false))) return;
  if (!user || !pass) {
    throw new Error(
      "GitHub login form visible but E2E_GITHUB_USERNAME/PASSWORD not set (or add github.com cookies to E2E_STORAGE_STATE)",
    );
  }
  await page.getByLabel(/Username or email/i).fill(user);
  await passwordField.fill(pass);
  await page.getByRole("button", { name: /^Sign in$/i }).click();
  await page.waitForLoadState("networkidle").catch(() => {});
}

export async function completeGitHubAppInstallWizard(
  page: Page,
  options: { consoleOrigin: RegExp | string },
) {
  await dismissCookieBanner(page);
  await ensureGitHubLogin(page);
  await dismissCookieBanner(page);

  const accountHint = process.env.E2E_GITHUB_INSTALL_ACCOUNT?.trim();
  if (accountHint) {
    const accountBtn = page.getByRole("button", { name: new RegExp(accountHint, "i") });
    const accountLink = page.getByRole("link", { name: new RegExp(accountHint, "i") });
    if (await accountBtn.isVisible().catch(() => false)) {
      await accountBtn.click();
    } else if (await accountLink.isVisible().catch(() => false)) {
      await accountLink.click();
    }
    await page.waitForLoadState("domcontentloaded");
  }

  const onlySelect = page.getByRole("radio", {
    name: /Only select repositories/i,
  });
  if (await onlySelect.isVisible().catch(() => false)) {
    await onlySelect.click();
    const repoFull = process.env.E2E_GITHUB_REPO_FULL_NAME?.trim();
    if (repoFull) {
      const search = page.getByPlaceholder(/search/i).first();
      if (await search.isVisible().catch(() => false)) {
        const [owner, repo] = repoFull.split("/");
        await search.fill(repo ?? repoFull);
        await page
          .getByRole("row", { name: new RegExp(repo ?? repoFull, "i") })
          .getByRole("checkbox")
          .first()
          .click({ timeout: 15_000 })
          .catch(async () => {
            await page.getByRole("checkbox").nth(1).click();
          });
      }
    }
  }

  const install = page.getByRole("button", { name: /^Install(\s|$)/i });
  const approveInstall = page.getByRole("button", {
    name: /^(Approve and install|Install)$/i,
  });
  const primary = install.or(approveInstall).first();
  await primary.click({ timeout: 60_000 });

  await page.waitForURL(options.consoleOrigin, { timeout: 120_000 });
}
