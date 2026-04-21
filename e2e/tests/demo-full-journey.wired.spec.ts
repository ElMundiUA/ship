import { expect, test, type Page } from "@playwright/test";

import { GH_API, ghHeaders, parseRepo } from "../lib/github-rest";
import {
  resetGithubSandboxRepo,
  resetShipWorkspace,
} from "../lib/reset";
import {
  hasShipApiCredentials,
  shipApiBase,
  shipApiToken,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * **Demo capture — not a regression test.**
 *
 * The "real" full-journey spec (``full-journey.wired.spec.ts``) is
 * deliberately permissive: it lets the wizard's auto-resume short-cut
 * through any step the backend already considers complete. That's
 * great for CI (asserts the contract regardless of state) but useless
 * for screen-recording — on a workspace that already has a tracker
 * wired, the wizard jumps straight to ``done`` in ~4s without
 * rendering the intermediate UI at all.
 *
 * This spec is the inverse: we *want* every screen on tape, even when
 * the backend would normally skip them. We pin each step via the
 * ``?step=…`` query param (``hasExplicitStep`` in the console wins
 * over auto-resume) and slow each transition enough to be readable on
 * a share-able recording.
 *
 * Run it with the demo config so video + slowMo are on:
 *
 *   E2E_RUN_DEMO_JOURNEY=1 \
 *   E2E_RUN_KNOWLEDGE_SEED=1 \
 *   npx playwright test demo-full-journey.wired.spec.ts \
 *     --config=playwright.demo.config.ts
 *
 * Outputs land under ``test-results/<test-name>/video.webm``; convert
 * to MP4 via the README "Record demo" recipe.
 *
 * ⚠ This spec **resets the sandbox** (closes ship-authored PRs/issues,
 * disconnects the sandbox repo, removes tracker integrations) before
 * driving the journey, so the knowledge-seed PR fires every run. The
 * production repo is untouched (``onlyRepoFullName`` narrows the
 * disconnect to ``E2E_SANDBOX_REPO``).
 */

const PRESET = process.env.E2E_PRESET?.trim() || "web-app";

function isOnStep(page: Page, re: RegExp) {
  return page.getByRole("heading", { name: re });
}

async function pinStep(page: Page, step: string, ws: string) {
  // Build a relative URL — Playwright resolves against ``baseURL``,
  // which sidesteps the ``page.url() === "about:blank"`` case before
  // any navigation has happened (``new URL(_, "about:blank")`` throws).
  const params = new URLSearchParams({ step, ws });
  await page.goto(`/onboarding?${params.toString()}`);
}

test.describe.configure({ mode: "serial" });

test.describe("demo: full onboarding journey", () => {
  test.describe.configure({ timeout: 8 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_DEMO_JOURNEY !== "1",
      "Set E2E_RUN_DEMO_JOURNEY=1 to record the demo journey",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE — see e2e/README.md",
    );
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/"),
      "Set E2E_SANDBOX_REPO=owner/name",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
    test.skip(
      !(process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN),
      "Set GITHUB_TOKEN for sandbox reset",
    );
  });

  test("@demo wizard renders github → repos → tracker → knowledge → done", async ({
    page,
    request,
  }) => {
    const sandbox = process.env.E2E_SANDBOX_REPO!.trim();
    const ghToken = (process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN)!;

    // --- 0. Reset sandbox so the journey is virgin --------------------
    const wsId = await shipResolveWorkspaceId(request);
    await test.step("reset sandbox (GitHub + Ship)", async () => {
      const gh = await resetGithubSandboxRepo(request, {
        repo: sandbox,
        token: ghToken,
        deleteBranches: true,
      });
      expect(gh.errors, gh.errors.join("; ")).toEqual([]);
      const ship = await resetShipWorkspace(request, {
        base: shipApiBase()!,
        token: shipApiToken()!,
        workspaceId: wsId,
        // Narrow to sandbox so production repos in shared workspaces
        // stay wired up. Tracker integrations are still cleared (no-op
        // if GitHub Issues which is implicit, DELETE 404 if absent).
        onlyRepoFullName: sandbox,
      });
      expect(ship.errors, ship.errors.join("; ")).toEqual([]);
    });

    // --- 1. Step "github" — show the install screen -------------------
    await test.step("step 1 · install Ship on GitHub", async () => {
      await pinStep(page, "github", wsId);
      // The install CTA may not render if the App is already installed
      // org-wide (the most common path for a returning workspace) — in
      // that case the wizard shows a "skip ahead" link. Race both.
      const ctaInstall = page.getByTestId("onboarding-install-github");
      const ctaSkip = page.getByRole("link", { name: /pick repos/i });
      await Promise.race([
        ctaInstall.waitFor({ state: "visible", timeout: 30_000 }),
        ctaSkip.waitFor({ state: "visible", timeout: 30_000 }),
      ]);
      await page.waitForTimeout(2000);
    });

    // --- 2. Step "repos" — preset + repo checkbox ---------------------
    await test.step("step 2 · pick preset + activate sandbox repo", async () => {
      await pinStep(page, "repos", wsId);
      await expect(
        isOnStep(page, /which repos should ship watch/i),
      ).toBeVisible({ timeout: 30_000 });
      await page.locator(`input[name="preset"][value="${PRESET}"]`).check();
      const target = page
        .locator("label", { hasText: sandbox })
        .locator('input[name="repo_id"]');
      await expect(target, `sandbox row "${sandbox}"`).toHaveCount(1, {
        timeout: 15_000,
      });
      if (!(await target.isChecked())) await target.check();
      await page.waitForTimeout(1200);
      await page.getByTestId("onboarding-wire-repos").click();
      // Wizard may bounce to tracker, knowledge, or done — accept all.
      await expect(
        isOnStep(page, /pick a tracker/i)
          .or(isOnStep(page, /give ship a head start/i))
          .or(page.getByTestId("onboarding-done-title")),
      ).toBeVisible({ timeout: 45_000 });
    });

    // --- 3. Step "tracker" — pin and click "Use GitHub Issues" --------
    await test.step("step 3 · connect tracker (GitHub Issues)", async () => {
      await pinStep(page, "tracker", wsId);
      await expect(isOnStep(page, /pick a tracker/i)).toBeVisible({
        timeout: 30_000,
      });
      await page.waitForTimeout(1500);
      await page
        .getByRole("button", { name: /use github issues/i })
        .click();
      // Optional Continue affordance — race past it if shown.
      const cont = page.getByTestId("onboarding-tracker-continue");
      if (await cont.isVisible().catch(() => false)) {
        await page.waitForTimeout(800);
        await cont.click();
      }
      await expect(
        isOnStep(page, /give ship a head start/i).or(
          page.getByTestId("onboarding-done-title"),
        ),
      ).toBeVisible({ timeout: 30_000 });
    });

    // --- 4. Step "knowledge" — pin + open seed PR (4-step wizards) ----
    //
    // The deployed dev console may still be on the 3-step flow (no
    // ``knowledge`` step). In that case pinning ``?step=knowledge``
    // is a no-op — ``pickStep`` doesn't recognise the value and the
    // wizard falls back to its default. We detect this by checking
    // whether the knowledge heading appears within a short window;
    // when it doesn't, we skip step 4 entirely and let step 5 record
    // the "done" card the wizard already rendered.
    let seededPrNumber: number | null = null;
    await test.step("step 4 · seed starter knowledge (if available)", async () => {
      await pinStep(page, "knowledge", wsId);
      const knowledgeHeading = isOnStep(page, /give ship a head start/i);
      const visible = await knowledgeHeading
        .waitFor({ state: "visible", timeout: 8_000 })
        .then(() => true)
        .catch(() => false);
      if (!visible) {
        test.info().annotations.push({
          type: "skip-knowledge",
          description:
            "Wizard does not recognise ?step=knowledge — deployed " +
            "console is on the 3-step flow. Run the demo locally " +
            "(npm run dev in console/) to capture the knowledge step.",
        });
        return;
      }
      await page.waitForTimeout(2500);
      if (process.env.E2E_RUN_KNOWLEDGE_SEED === "1") {
        await page.getByTestId("onboarding-knowledge-seed").click();
      } else {
        await page.getByTestId("onboarding-knowledge-skip").click();
      }
      await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
        timeout: 60_000,
      });
      const u = page.url();
      const prParam = u.includes("?")
        ? new URLSearchParams(u.split("?")[1] ?? "").get("pr")
        : null;
      if (prParam && /^\d+$/.test(prParam)) {
        seededPrNumber = Number.parseInt(prParam, 10);
      }
    });

    // --- 5. Step "done" — linger so the recording captures the card ---
    await test.step("step 5 · done", async () => {
      // Pin so we land on done deterministically, regardless of where
      // step 3 / step 4 left us (some wizard variants stay on tracker
      // when GitHub Issues is the implicit tracker).
      await pinStep(page, "done", wsId);
      await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
        timeout: 30_000,
      });
      await page.waitForTimeout(3000);
    });

    // --- 6. Sanity: PR landed (when seeded) ---------------------------
    if (seededPrNumber !== null) {
      await test.step(`PR #${seededPrNumber} on GitHub`, async () => {
        const r = parseRepo(sandbox);
        expect(r).not.toBeNull();
        const { owner, repo } = r!;
        const prRes = await fetch(
          `${GH_API}/repos/${owner}/${repo}/pulls/${seededPrNumber}`,
          { headers: ghHeaders(ghToken) },
        );
        expect(prRes.ok, `GitHub PR ${prRes.status}`).toBe(true);
      });
    }
  });
});
