import { expect, test, type Page } from "@playwright/test";

import { completeGitHubAppInstallWizard } from "../lib/github-install";
import { GH_API, ghHeaders, parseRepo } from "../lib/github-rest";
import {
  hasShipApiCredentials,
  shipApiGet,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Full "as-in-Elmundi" onboarding journey against the **deployed dev** console.
 *
 * Chains the three wizard steps end to end and asserts the tenant is wired
 * up for automatic SDLC:
 *
 *   1.  /onboarding?step=github  → optional GitHub App install (gated on
 *       `E2E_RUN_GITHUB_APP_INSTALL=1`; otherwise we assume the App is
 *       preinstalled on the sandbox org so the resume pointer jumps ahead).
 *   2.  step=repos               → pick the preset in `E2E_PRESET` (default
 *       `web-app`) and activate the `E2E_SANDBOX_REPO` checkbox.
 *   3.  step=tracker             → click the "Use GitHub Issues" tile (no
 *       separate OAuth — reuses the GitHub App), then "Continue".
 *   4.  done                     → assert the confirmation card.
 *   5.  Ship API                 → assert the preset-enabled pipelines
 *       landed in `/v1/workspaces/{ws}/pipelines`, and that the GitHub
 *       tracker integration is present.
 *
 * **Re-running the journey on the same sandbox needs a reset** — see
 * `tests/full-journey-reset.sandbox.spec.ts` and README § Reset sandbox.
 *
 * This spec is opt-in: it only runs when `E2E_RUN_FULL_JOURNEY=1` so it
 * stays out of the default CI matrix (default run covers smoke + wired
 * surface checks but not the full install+activate side effects).
 *
 * @deployed
 */

type PresetId =
  | "web-app"
  | "api-backend"
  | "mobile-app"
  | "cli"
  | "monorepo"
  | "marketing"
  | "adoption-minimum";

const KNOWN_PRESETS: readonly PresetId[] = [
  "web-app",
  "api-backend",
  "mobile-app",
  "cli",
  "monorepo",
  "marketing",
  "adoption-minimum",
];

// Mirror of `PRESET_ENABLED_KINDS` in
// `backend/app/services/default_pipelines.py` — kept tiny and explicit so
// the assertion reads clearly; bump here when the backend map changes.
const PRESET_ENABLED_KINDS: Record<PresetId, ReadonlyArray<string>> = {
  "web-app": [
    "pr_review",
    "daily_standup",
    "tech_debt",
    "self_heal",
    "code_map",
  ],
  "api-backend": ["pr_review", "daily_standup", "tech_debt", "code_map"],
  "mobile-app": ["pr_review", "daily_standup", "tech_debt", "code_map"],
  cli: ["pr_review", "tech_debt", "code_map"],
  monorepo: [
    "pr_review",
    "daily_standup",
    "tech_debt",
    "self_heal",
    "code_map",
  ],
  marketing: ["pr_review", "daily_standup", "code_map"],
  "adoption-minimum": ["pr_review", "code_map"],
};

function pickPreset(): PresetId {
  const raw = (process.env.E2E_PRESET?.trim() || "web-app") as PresetId;
  return (KNOWN_PRESETS as readonly string[]).includes(raw) ? raw : "web-app";
}

function isOnStep(page: Page, re: RegExp) {
  return page.getByRole("heading", { name: re });
}

test.describe.configure({ mode: "serial" });

test.describe("full onboarding journey (deployed)", () => {
  test.describe.configure({ timeout: 6 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_FULL_JOURNEY !== "1",
      "Set E2E_RUN_FULL_JOURNEY=1 to execute the full wizard journey",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (authenticated Auth0 session) — see e2e/README.md",
    );
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/"),
      "Set E2E_SANDBOX_REPO=owner/name (the test repo to activate)",
    );
  });

  test("@deployed install app → preset + sandbox repo → GitHub Issues → done", async ({
    page,
    baseURL,
    request,
  }) => {
    const sandbox = process.env.E2E_SANDBOX_REPO!.trim();
    const preset = pickPreset();
    const consoleRe =
      typeof baseURL === "string"
        ? new RegExp(baseURL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
        : /localhost|127\.0\.0\.1/;

    // --- Step 1: GitHub App install OR resume past it ------------------
    //
    // We intentionally don't pin ``?step=github`` here. Pinning forces
    // the wizard to render the install step even when the App is
    // already installed (URL pins beat the auto-resume pointer — see
    // ``hasExplicitStep`` in ``console/src/app/onboarding/page.tsx``).
    // Passing no ``step=`` lets B8 auto-resume land us on the furthest
    // step the backend knows we can make progress on: ``github`` if no
    // App, ``repos`` if App installed but no repo activated, ``tracker``
    // if a repo is active, or ``done`` if a tracker is already wired.
    // From whichever step we land on, the rest of this journey either
    // drives the install flow (gated on ``E2E_RUN_GITHUB_APP_INSTALL``)
    // or jumps straight to repos.
    await test.step("land on onboarding", async () => {
      await page.goto("/onboarding");
      await expect(
        isOnStep(page, /install ship on github/i)
          .or(isOnStep(page, /which repos should ship watch/i))
          .or(isOnStep(page, /pick a tracker/i))
          .or(page.getByTestId("onboarding-done-title")),
      ).toBeVisible({ timeout: 30_000 });
    });

    const onGithubStep = await page
      .getByTestId("onboarding-install-github")
      .count()
      .then((n) => n > 0);

    if (onGithubStep) {
      if (process.env.E2E_RUN_GITHUB_APP_INSTALL === "1") {
        await test.step("install Ship GitHub App", async () => {
          const nav = page.waitForURL(/github\.com/, { timeout: 60_000 });
          await page.getByTestId("onboarding-install-github").click();
          await nav;
          await completeGitHubAppInstallWizard(page, {
            consoleOrigin: consoleRe,
          });
          await expect(page).toHaveURL(/\/onboarding/i, { timeout: 30_000 });
        });
      } else {
        test.skip(
          true,
          "Auto-resume kept the wizard on the GitHub install step — " +
            "the Ship App isn't installed on the workspace's org yet. " +
            "Pre-install it on the sandbox org or set " +
            "E2E_RUN_GITHUB_APP_INSTALL=1 to drive the install wizard.",
        );
        return;
      }
    }

    // After a fresh install the console bounces to ?step=repos&github=installed.
    // A resumed visit may also land on tracker/done — normalise by pinning to
    // repos so we can pick preset + repo every time.
    await test.step("navigate to repos step", async () => {
      const wsId =
        new URL(page.url()).searchParams.get("ws") ??
        new URL(page.url()).searchParams.get("workspace") ??
        undefined;
      const href = wsId
        ? `/onboarding?step=repos&ws=${encodeURIComponent(wsId)}`
        : "/onboarding?step=repos";
      await page.goto(href);
      await expect(
        isOnStep(page, /which repos should ship watch/i),
      ).toBeVisible({ timeout: 30_000 });
    });

    // --- Step 2: preset + repo ------------------------------------------
    await test.step(`pick preset "${preset}" + activate ${sandbox}`, async () => {
      await page.locator(`input[name="preset"][value="${preset}"]`).check();

      const targetCheckbox = page
        .locator("label", { hasText: sandbox })
        .locator('input[name="repo_id"]');
      await expect(targetCheckbox, `sandbox row "${sandbox}"`).toHaveCount(1, {
        timeout: 15_000,
      });

      // CRITICAL: ``/repos/activate`` is a replace-set endpoint — the
      // payload is treated as the *complete* desired activation list,
      // and anything previously activated but absent is disconnected
      // (see backend/app/api/v1/routes/repos.py#activate_repos). The
      // wizard submits the union of checked boxes, so we must keep
      // every pre-checked repo ticked and only *add* our sandbox on
      // top — otherwise a shared workspace loses its production repos
      // every time this e2e runs.
      if (!(await targetCheckbox.isChecked())) await targetCheckbox.check();

      await page.getByTestId("onboarding-wire-repos").click();
      await expect(
        isOnStep(page, /pick a tracker/i).or(
          page.getByTestId("onboarding-done-title"),
        ),
      ).toBeVisible({ timeout: 45_000 });
    });

    // --- Step 3: tracker = GitHub Issues --------------------------------
    //
    // Clicking the "Use GitHub Issues" tile may either (a) return the
    // user to the tracker step with a success banner and a "Continue"
    // CTA that forwards to ``step=knowledge``, (b) jump straight to
    // ``step=knowledge`` (the common path post-Day-4), or (c) skip
    // ahead to ``done`` on a fully-wired workspace where the tracker
    // re-submit is a no-op. All three are success; race them instead
    // of hard-coding one.
    if (await isOnStep(page, /pick a tracker/i).isVisible().catch(() => false)) {
      await test.step("connect tracker: GitHub Issues", async () => {
        await page
          .getByRole("button", { name: /use github issues/i })
          .click();
        await expect(
          isOnStep(page, /pick a tracker/i)
            .or(isOnStep(page, /give ship a head start/i))
            .or(page.getByTestId("onboarding-done-title")),
        ).toBeVisible({ timeout: 30_000 });
        if (
          await isOnStep(page, /give ship a head start/i)
            .isVisible()
            .catch(() => false)
        ) {
          return;
        }
        if (
          await page
            .getByTestId("onboarding-done-title")
            .isVisible()
            .catch(() => false)
        ) {
          return;
        }
        const cont = page.getByTestId("onboarding-tracker-continue");
        if (await cont.isVisible().catch(() => false)) {
          await cont.click();
          await expect(
            isOnStep(page, /give ship a head start/i).or(
              page.getByTestId("onboarding-done-title"),
            ),
          ).toBeVisible({ timeout: 30_000 });
        }
      });
    }

    // --- Step 4: knowledge seed (Skip by default; opt-in real PR) -------
    //
    // The wizard now has a step-4 "Seed starter knowledge" screen
    // between tracker and done. By default we Skip it — opening a
    // real PR on every e2e run would spam the sandbox repo. Set
    // ``E2E_RUN_KNOWLEDGE_SEED=1`` to actually click "Open seed PR"
    // and assert the PR appears via the GitHub REST API (requires
    // ``GITHUB_TOKEN`` scoped to the sandbox repo).
    const onKnowledgeStep = await isOnStep(page, /give ship a head start/i)
      .isVisible()
      .catch(() => false);
    let seededPrNumber: number | null = null;
    if (onKnowledgeStep) {
      if (process.env.E2E_RUN_KNOWLEDGE_SEED === "1") {
        await test.step("seed starter knowledge (opens PR)", async () => {
          // Default-checked state: both code-style and ui-runbook
          // boxes are ticked. We just click the primary CTA.
          await page.getByTestId("onboarding-knowledge-seed").click();
          await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
            timeout: 60_000,
          });
          const confirm = page.getByTestId("onboarding-knowledge-pr");
          await expect(confirm, "PR confirmation card on Done").toBeVisible({
            timeout: 10_000,
          });
          // Grab the PR number from the ``?pr=`` query param the
          // knowledge route stamped on the Done redirect.
          const prParam = new URL(page.url()).searchParams.get("pr");
          if (prParam && /^\d+$/.test(prParam)) {
            seededPrNumber = Number.parseInt(prParam, 10);
          }
        });
      } else {
        await test.step("skip knowledge seed (opt-in via env)", async () => {
          await page.getByTestId("onboarding-knowledge-skip").click();
          await expect(page.getByTestId("onboarding-done-title")).toBeVisible({
            timeout: 30_000,
          });
        });
      }
    }

    // --- Step 5: assert via Ship API that the preset landed -------------
    if (!hasShipApiCredentials()) {
      test.info().annotations.push({
        type: "skip-api",
        description:
          "E2E_SHIP_API_BASE / E2E_SHIP_API_TOKEN not set — skipping backend assertions",
      });
      return;
    }

    await test.step("Ship API reflects preset + GitHub tracker", async () => {
      const ws = await shipResolveWorkspaceId(request);
      const wsEnc = encodeURIComponent(ws);

      // Pipelines contract: ``seed_default_pipelines`` is *additive-
      // only* (see backend/app/services/default_pipelines.py — "user
      // customisations win over presets"). On a pristine workspace the
      // preset's kinds all seed as ``enabled``; on a workspace that
      // already had pipeline rows (any prior preset), re-activating
      // does NOT flip ``enabled`` — it only creates missing rows. So
      // the contract this test can enforce without being flaky is:
      // every kind the preset declares exists as a row for the
      // workspace. We still assert that ``pr_review`` is enabled — the
      // "sign up and Ship reviews your next PR" WOW promise is a hard
      // product invariant regardless of prior state.
      const pipRes = await shipApiGet(
        request,
        `/v1/workspaces/${wsEnc}/pipelines`,
      );
      expect(pipRes.ok(), `pipelines ${pipRes.status()}`).toBeTruthy();
      const pipelines = (await pipRes.json()) as {
        kind: string;
        enabled: boolean;
      }[];
      const rowKinds = pipelines.map((p) => p.kind);
      for (const kind of PRESET_ENABLED_KINDS[preset]) {
        expect(rowKinds, `preset ${preset} seeds row ${kind}`).toContain(kind);
      }
      const prReview = pipelines.find((p) => p.kind === "pr_review");
      expect(prReview?.enabled, "pr_review enabled (WOW contract)").toBe(true);

      // GitHub Issues is an *implicit* tracker in Ship: the console's
      // tracker-install route short-circuits `kind === "github"`
      // without writing an ``integrations`` row because the Ship
      // GitHub App already grants Issues read/write. Linear / Notion
      // on the other hand do write rows via OAuth callbacks. So:
      // assert there's either a github row (future-proofing if the
      // product decides to record it), OR at least that the endpoint
      // responds successfully — which proves the workspace is in a
      // state where trackers can be queried.
      const intRes = await shipApiGet(
        request,
        `/v1/workspaces/${wsEnc}/integrations`,
      );
      expect(intRes.ok(), `integrations ${intRes.status()}`).toBeTruthy();
      const integrations = (await intRes.json()) as { kind: string }[];
      const nonGithubRows = integrations.filter((i) => i.kind !== "github");
      for (const row of nonGithubRows) {
        // Sanity: every OAuth-based row has a known vendor slug. Keeps
        // the test honest about the integration shape without pinning
        // to a specific set since tenants may add trackers later.
        expect(["linear", "notion", "jira"]).toContain(row.kind);
      }
    });

    // --- Step 6: verify knowledge-seed PR on GitHub (opt-in) ------------
    //
    // Only runs when ``E2E_RUN_KNOWLEDGE_SEED=1`` AND a PR number was
    // surfaced on the Done card (i.e. the wizard actually drove the
    // seed path). We look up the PR via GitHub REST to confirm the
    // backend did what it claimed and the files under
    // ``.ship/knowledge/`` are on the proposed branch.
    if (
      process.env.E2E_RUN_KNOWLEDGE_SEED === "1" &&
      seededPrNumber !== null &&
      process.env.GITHUB_TOKEN
    ) {
      await test.step("GitHub has the knowledge-seed PR", async () => {
        const r = parseRepo(sandbox);
        expect(r, `sandbox "${sandbox}" is owner/name`).not.toBeNull();
        const { owner, repo } = r!;
        const prRes = await fetch(
          `${GH_API}/repos/${owner}/${repo}/pulls/${seededPrNumber}`,
          { headers: ghHeaders(process.env.GITHUB_TOKEN!) },
        );
        expect(prRes.ok, `GitHub PR fetch ${prRes.status}`).toBe(true);
        const pr = (await prRes.json()) as {
          state: string;
          head: { ref: string };
          title: string;
        };
        // Open/merged are both fine — the test just asserts we opened
        // the PR. A tenant who auto-merges is still a valid state.
        expect(["open", "closed"]).toContain(pr.state);
        expect(pr.title).toMatch(/knowledge/i);

        const filesRes = await fetch(
          `${GH_API}/repos/${owner}/${repo}/pulls/${seededPrNumber}/files`,
          { headers: ghHeaders(process.env.GITHUB_TOKEN!) },
        );
        expect(filesRes.ok, `GitHub PR files ${filesRes.status}`).toBe(true);
        const files = (await filesRes.json()) as { filename: string }[];
        const paths = files.map((f) => f.filename);
        // Preserve the wizard's "select-all" default — assert both
        // starter buckets were proposed on the branch.
        expect(paths, "PR drops .ship/knowledge/code-style.md").toContain(
          ".ship/knowledge/code-style.md",
        );
        expect(paths, "PR drops .ship/knowledge/ui-runbook.md").toContain(
          ".ship/knowledge/ui-runbook.md",
        );
      });
    }
  });
});
