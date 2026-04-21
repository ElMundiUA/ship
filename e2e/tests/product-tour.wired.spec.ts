import { expect, test, type Page } from "@playwright/test";

import { resetGithubSandboxRepo, resetShipWorkspace } from "../lib/reset";
import {
  hasShipApiCredentials,
  shipApiBase,
  shipApiGet,
  shipApiToken,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * **Product tour — single-take demo recording.**
 *
 * Walks the operator across every screen we want on tape, in one
 * continuous Playwright test (one test == one ``video.webm``). Use
 * the demo config so video / slowMo / 1440x900 viewport are on:
 *
 *   E2E_RUN_PRODUCT_TOUR=1 \
 *   E2E_TOUR_INCLUDE_WIZARD=1 \
 *   E2E_RUN_KNOWLEDGE_SEED=1 \
 *   npx playwright test product-tour.wired.spec.ts \
 *     --config=playwright.demo.config.ts
 *
 * The tour script (each step lingers ~2.5s so the recording reads):
 *
 *   0. (opt-in) Reset the sandbox + run the onboarding wizard end to
 *      end so the recording opens with the full install flow.
 *   1. Dashboard (`/`)                  — operating KPIs + recent runs
 *   2. Pipelines (`/pipelines`)         — repo-grouped swimlanes
 *   3. Pipeline run (`/pipelines/.../runs/...`) — single run detail
 *   4. Clarifications (`/clarifications`) — tracker-projected items
 *   5. Improvements (`/improvements`)   — agent proposals
 *   6. Feedback (`/artifact-feedback`)  — catalog feedback inbox
 *   7. Navigator (`/chat`)              — agent chat + buckets sidebar
 *   8. Knowledge (`/knowledge`)         — bucket grid
 *   9. Catalog (`/catalog`)             — artifact catalog
 *  10. Repo secrets (`/repos/<id>/secrets`) — Ship-managed Actions
 *  11. Metrics (`/metrics`)             — DORA-ish window toggles
 *  12. Settings (`/settings`)           — workspace tabs
 *  13. Members (`/members`)             — roster
 *  14. Integrations (`/integrations`)   — connected vs available
 *  15. Audit (`/audit`)                 — audit log
 *  16. Back to dashboard                — final card on tape
 *
 * Knobs:
 *   - E2E_TOUR_INCLUDE_WIZARD=1  — prepend the wizard tour (resets
 *     the sandbox first, so this opt-in keeps casual recordings
 *     non-destructive).
 *   - E2E_TOUR_DWELL_MS=N        — per-screen pause (default 2500).
 *   - E2E_RUN_KNOWLEDGE_SEED=1   — open the seed PR during the
 *     wizard portion (only hits if the deployed console is on the
 *     4-step flow).
 */

const DWELL = Number.parseInt(process.env.E2E_TOUR_DWELL_MS ?? "2500", 10);
const PRESET = process.env.E2E_PRESET?.trim() || "web-app";

function isOnHeading(page: Page, name: string | RegExp) {
  return page.getByRole("heading", { name, exact: typeof name === "string" });
}

async function pinStep(page: Page, step: string, ws: string) {
  const params = new URLSearchParams({ step, ws });
  await page.goto(`/onboarding?${params.toString()}`);
}

async function visit(
  page: Page,
  url: string,
  heading: string | RegExp,
  opts: { dwell?: number; allowMissing?: boolean } = {},
) {
  await page.goto(url);
  const head = isOnHeading(page, heading);
  const visible = await head
    .first()
    .waitFor({ state: "visible", timeout: 20_000 })
    .then(() => true)
    .catch(() => false);
  if (!visible && !opts.allowMissing) {
    throw new Error(`tour: heading not visible at ${url} — ${heading}`);
  }
  await page.waitForTimeout(opts.dwell ?? DWELL);
}

test.describe.configure({ mode: "serial" });

test.describe("product tour (deployed dev)", () => {
  test.describe.configure({ timeout: 12 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_PRODUCT_TOUR !== "1",
      "Set E2E_RUN_PRODUCT_TOUR=1 to record the product tour",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE — see e2e/README.md",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  test("@demo single-take walk through the whole product", async ({
    page,
    request,
  }) => {
    const wsId = await shipResolveWorkspaceId(request);
    const wsEnc = encodeURIComponent(wsId);
    const sandbox = process.env.E2E_SANDBOX_REPO?.trim() || "";
    const ghToken =
      process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN || "";

    // -----------------------------------------------------------------
    // 0. Optional: reset sandbox + drive wizard
    // -----------------------------------------------------------------
    if (process.env.E2E_TOUR_INCLUDE_WIZARD === "1") {
      test.skip(
        !sandbox.includes("/"),
        "Wizard tour needs E2E_SANDBOX_REPO=owner/name",
      );
      test.skip(!ghToken, "Wizard tour needs GITHUB_TOKEN for sandbox reset");

      await test.step("reset sandbox before wizard", async () => {
        const gh = await resetGithubSandboxRepo(request, {
          repo: sandbox,
          token: ghToken,
          deleteBranches: true,
        });
        expect(gh.errors).toEqual([]);
        const ship = await resetShipWorkspace(request, {
          base: shipApiBase()!,
          token: shipApiToken()!,
          workspaceId: wsId,
          onlyRepoFullName: sandbox,
        });
        expect(ship.errors).toEqual([]);
      });

      await test.step("wizard step 1 · install Ship on GitHub", async () => {
        await pinStep(page, "github", wsId);
        const cta = page.getByTestId("onboarding-install-github");
        const skip = page.getByRole("link", { name: /pick repos/i });
        await Promise.race([
          cta.waitFor({ state: "visible", timeout: 30_000 }),
          skip.waitFor({ state: "visible", timeout: 30_000 }),
        ]);
        await page.waitForTimeout(DWELL);
      });

      await test.step("wizard step 2 · pick preset + sandbox repo", async () => {
        await pinStep(page, "repos", wsId);
        await expect(
          isOnHeading(page, /which repos should ship watch/i),
        ).toBeVisible({ timeout: 30_000 });
        await page.locator(`input[name="preset"][value="${PRESET}"]`).check();
        const target = page
          .locator("label", { hasText: sandbox })
          .locator('input[name="repo_id"]');
        await expect(target).toHaveCount(1, { timeout: 15_000 });
        if (!(await target.isChecked())) await target.check();
        await page.waitForTimeout(DWELL);
        await page.getByTestId("onboarding-wire-repos").click();
        await expect(
          isOnHeading(page, /pick a tracker/i)
            .or(isOnHeading(page, /give ship a head start/i))
            .or(page.getByTestId("onboarding-done-title")),
        ).toBeVisible({ timeout: 45_000 });
      });

      await test.step("wizard step 3 · connect tracker", async () => {
        await pinStep(page, "tracker", wsId);
        await expect(isOnHeading(page, /pick a tracker/i)).toBeVisible({
          timeout: 30_000,
        });
        await page.waitForTimeout(DWELL);
        await page
          .getByRole("button", { name: /use github issues/i })
          .click();
        const cont = page.getByTestId("onboarding-tracker-continue");
        if (await cont.isVisible().catch(() => false)) {
          await page.waitForTimeout(800);
          await cont.click();
        }
        await expect(
          isOnHeading(page, /give ship a head start/i).or(
            page.getByTestId("onboarding-done-title"),
          ),
        ).toBeVisible({ timeout: 30_000 });
      });

      await test.step("wizard step 4 · seed knowledge (if available)", async () => {
        await pinStep(page, "knowledge", wsId);
        const knowledge = isOnHeading(page, /give ship a head start/i);
        const visible = await knowledge
          .waitFor({ state: "visible", timeout: 8_000 })
          .then(() => true)
          .catch(() => false);
        if (!visible) {
          test.info().annotations.push({
            type: "skip-knowledge",
            description:
              "Deployed wizard is on the 3-step flow — no knowledge step.",
          });
          return;
        }
        await page.waitForTimeout(DWELL);
        if (process.env.E2E_RUN_KNOWLEDGE_SEED === "1") {
          await page.getByTestId("onboarding-knowledge-seed").click();
        } else {
          await page.getByTestId("onboarding-knowledge-skip").click();
        }
        await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
          timeout: 60_000,
        });
      });

      await test.step("wizard step 5 · done", async () => {
        await pinStep(page, "done", wsId);
        await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
          timeout: 30_000,
        });
        await page.waitForTimeout(DWELL);
      });
    }

    // -----------------------------------------------------------------
    // 1. Dashboard
    // -----------------------------------------------------------------
    await visit(page, "/", "Operating dashboard");

    // -----------------------------------------------------------------
    // 2. Pipelines (repo-grouped swimlanes — proves workflows installed)
    // -----------------------------------------------------------------
    await visit(page, "/pipelines", "Pipelines");

    // -----------------------------------------------------------------
    // 3. Pipeline run detail — pick the most recent run via API.
    // -----------------------------------------------------------------
    await test.step("pipeline run detail (most recent)", async () => {
      try {
        const res = await shipApiGet(
          request,
          `/v1/workspaces/${wsEnc}/pipelines`,
        );
        if (!res.ok()) return;
        const pipelines = (await res.json()) as { id: string }[];
        for (const p of pipelines) {
          const runsRes = await shipApiGet(
            request,
            `/v1/workspaces/${wsEnc}/pipelines/${encodeURIComponent(
              p.id,
            )}/runs?limit=1`,
          );
          if (!runsRes.ok()) continue;
          const runs = (await runsRes.json()) as { id: string }[];
          if (!runs.length) continue;
          const url = `/pipelines/${p.id}/runs/${runs[0].id}?ws=${wsEnc}`;
          await visit(page, url, "Pipeline run", { allowMissing: true });
          return;
        }
      } catch {
        /* tour-friendly: skip if API hiccups */
      }
    });

    // -----------------------------------------------------------------
    // 4. Clarifications (tracker-projected items)
    // -----------------------------------------------------------------
    await visit(page, "/clarifications", "Clarifications");

    // -----------------------------------------------------------------
    // 5. Improvements
    // -----------------------------------------------------------------
    await visit(page, "/improvements", "Improvements");

    // -----------------------------------------------------------------
    // 6. Artifact feedback
    // -----------------------------------------------------------------
    await visit(page, "/artifact-feedback", "Artifact feedback");

    // -----------------------------------------------------------------
    // 7. Navigator (agent chat) — type a sample message into composer.
    // -----------------------------------------------------------------
    await test.step("navigator", async () => {
      await page.goto("/chat");
      await expect(isOnHeading(page, "Navigator")).toBeVisible({
        timeout: 20_000,
      });
      // Composer may be missing if LLM not configured — handle both.
      const composer = page.getByPlaceholder(/ask the agent/i);
      const composerVisible = await composer
        .waitFor({ state: "visible", timeout: 4_000 })
        .then(() => true)
        .catch(() => false);
      if (composerVisible) {
        await composer.fill(
          "Show me what changed in the last week across our pipelines.",
        );
        await page.waitForTimeout(DWELL);
      } else {
        await page.waitForTimeout(DWELL);
      }
    });

    // -----------------------------------------------------------------
    // 8–9. Knowledge + Catalog
    // -----------------------------------------------------------------
    await visit(page, "/knowledge", /knowledge/i);
    await visit(page, "/catalog", "Artifact catalog");

    // -----------------------------------------------------------------
    // 10. Repo secrets (Ship-managed Actions secrets) — first activated.
    // -----------------------------------------------------------------
    await test.step("repo secrets (first activated)", async () => {
      try {
        const res = await shipApiGet(request, `/v1/workspaces/${wsEnc}/repos`);
        if (!res.ok()) return;
        const repos = (await res.json()) as { id: string }[];
        if (!repos.length) return;
        await visit(page, `/repos/${repos[0].id}/secrets`, /secrets/i, {
          allowMissing: true,
        });
      } catch {
        /* skip on API hiccup */
      }
    });

    // -----------------------------------------------------------------
    // 11. Metrics — show the 30d window
    // -----------------------------------------------------------------
    await visit(page, "/metrics?window=30d", "Metrics");

    // -----------------------------------------------------------------
    // 12. Settings (with tab=tokens — pretty for demo)
    // -----------------------------------------------------------------
    await visit(page, "/settings?tab=tokens", "Workspace settings");

    // -----------------------------------------------------------------
    // 13. Members
    // -----------------------------------------------------------------
    await visit(page, "/members", "Members");

    // -----------------------------------------------------------------
    // 14. Integrations
    // -----------------------------------------------------------------
    await visit(page, "/integrations", "Integrations");

    // -----------------------------------------------------------------
    // 15. Audit log
    // -----------------------------------------------------------------
    await visit(page, "/audit", "Audit log");

    // -----------------------------------------------------------------
    // 16. Back to dashboard — close on the home card.
    // -----------------------------------------------------------------
    await visit(page, "/", "Operating dashboard", { dwell: DWELL + 1500 });
  });
});
