/**
 * Fixture builders + Playwright route helpers for the onboarding wizard
 * e2e tests (P5-10).
 *
 * Notes on scope: Playwright's :func:`Page.route` only intercepts
 * requests originated by the browser. The Wave-8c onboarding wizard
 * SSRs most of its state (workspaces, activated repos, default
 * bundle, tracker bindings) from server components — those fetches
 * leave the Next.js server directly and are NOT interceptable here.
 *
 * The mock helpers below cover the **client-side** routes the done
 * page actually calls in the browser:
 *
 *   - ``/api/onboard/wizard-seed-latest`` (fallback when
 *     sessionStorage is empty)
 *   - ``/api/onboard/intel-current``      (5s poll for repo intel)
 *   - ``/api/onboard/intel-harvest``      (manual retry POST)
 *
 * For SSR-driven assertions (bundle items, repo cards on the
 * Confirm step, DoneResult cards on the Done step) the test must
 * run against a live backend with a workspace that already owns at
 * least one activated repo. When that's missing, the wired tests
 * skip via the existing ``hasPlaywrightStorageState`` gate and a
 * lightweight runtime probe.
 */

import type { Page, Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Type echoes — kept loose so the helper file doesn't pull console types
// into the e2e tsconfig. Mirrors ``console/src/lib/api/client.ts`` shapes
// and ``backend/app/api/v1/schemas.py`` payloads.
// ---------------------------------------------------------------------------

export type WizardSeedCodeownersSummary = {
  file_found: boolean;
  rules_count: number;
  routing_rules_created: number;
  unresolved_owners: string[];
};

export type WizardSeedIntelHandle = {
  enqueued: boolean;
  job_id: string | null;
  intel_id: string | null;
};

export type WizardSeedResult = {
  pr_url: string;
  pr_number: number;
  branch: string;
  files: string[];
  presets: string[];
  knowledge_slugs: string[];
  tracker_kind: string | null;
  run_token_prefix: string | null;
  run_token_rotated: boolean;
  codeowners: WizardSeedCodeownersSummary | null;
  intel: WizardSeedIntelHandle | null;
  synthetic_lanes_created: number;
};

export type DefaultBundleEntry = {
  key: string;
  title: string;
  reason: string;
};

export type ActivatedRepo = {
  id: string;
  external_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
  description: string | null;
  activated_at: string | null;
  provider: string;
  preset: string | null;
  installed_bundle_version: number | null;
  current_bundle_version: number;
};

export type Workspace = {
  id: string;
  org_id: string;
  slug: string;
  name: string;
  catalog_sources: Record<string, boolean>;
  created_at: string;
};

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

export function buildWorkspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: "ws_test_0001",
    org_id: "org_test_0001",
    slug: "acme",
    name: "Acme",
    catalog_sources: { core: true },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function buildActivatedRepo(
  overrides: Partial<ActivatedRepo> = {},
): ActivatedRepo {
  return {
    id: "repo_test_0001",
    external_id: 12345,
    full_name: "acme/widgets",
    default_branch: "main",
    private: false,
    html_url: "https://github.com/acme/widgets",
    description: "Acme's widget factory",
    activated_at: "2026-02-01T00:00:00Z",
    provider: "github",
    preset: "default",
    installed_bundle_version: 1,
    current_bundle_version: 1,
    ...overrides,
  };
}

export function buildDefaultBundle(): DefaultBundleEntry[] {
  return [
    {
      key: "scan-tests",
      title: "Run tests",
      reason: "PR-attached gate that runs your test suite on every push.",
    },
    {
      key: "scan-types",
      title: "Type-check",
      reason: "Catches type drift on every PR before it lands.",
    },
    {
      key: "scan-lint",
      title: "Lint",
      reason: "Style & lint guardrails enforced on changed files.",
    },
    {
      key: "scan-security",
      title: "Security scan",
      reason: "Static analysis for known CVE patterns and secret leaks.",
    },
    {
      key: "scan-coverage",
      title: "Patch coverage",
      reason: "Gates merges on coverage of the diff, not the whole repo.",
    },
    {
      key: "knowledge-seed",
      title: "Knowledge starters",
      reason: "Drops .ship/knowledge/* starters so agents have context day one.",
    },
  ];
}

export function buildWizardSeedResult(
  overrides: Partial<WizardSeedResult> = {},
): WizardSeedResult {
  return {
    pr_url: "https://github.com/acme/widgets/pull/1234",
    pr_number: 1234,
    branch: "ship/bundle-bootstrap-1",
    files: [
      "/.ship/config.yml",
      "/.github/workflows/pr-and-ci-gate.yml",
      "/.ship/knowledge/repo-intel.md",
    ],
    presets: ["default"],
    knowledge_slugs: ["code-style", "ui-runbook"],
    tracker_kind: null,
    run_token_prefix: null,
    run_token_rotated: false,
    codeowners: {
      file_found: true,
      rules_count: 3,
      routing_rules_created: 2,
      unresolved_owners: ["@ext"],
    },
    intel: {
      enqueued: true,
      job_id: "arq:job:abc",
      intel_id: null,
    },
    synthetic_lanes_created: 7,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Route mocks — client-side wizard endpoints only (see file header)
// ---------------------------------------------------------------------------

const JSON_HEADERS = { "content-type": "application/json" };

function fulfillJson(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
}

/**
 * Mock the ``GET /api/onboard/wizard-seed-latest`` route handler.
 *
 * - ``result === null`` → returns 404 ``{error: "not_found"}`` so the
 *   FE renders the "no bootstrap yet" empty state.
 * - otherwise serves ``{result}`` with status 200, mirroring the
 *   live route's contract.
 */
export async function mockWizardSeedLatest(
  page: Page,
  result: WizardSeedResult | null,
): Promise<void> {
  await page.route("**/api/onboard/wizard-seed-latest*", async (route) => {
    if (result == null) {
      await fulfillJson(route, 404, { error: "not_found" });
      return;
    }
    await fulfillJson(route, 200, { result });
  });
}

/**
 * Mock the ``GET /api/onboard/intel-current`` poll endpoint.
 *
 * Default behaviour: serve 404 forever so the badge stays in the
 * ``harvesting`` state without flipping to ``done``. Pass a fixture
 * to flip it to ``done`` immediately, or ``"failed"`` to trigger
 * the ``failed`` state via ``harvest_error``.
 */
export async function mockIntelCurrent(
  page: Page,
  mode: "missing" | "ready" | "failed" = "missing",
): Promise<void> {
  await page.route("**/api/onboard/intel-current*", async (route) => {
    if (mode === "missing") {
      await fulfillJson(route, 404, { error: "not_found" });
      return;
    }
    if (mode === "failed") {
      await fulfillJson(route, 200, {
        intel: {
          intel_id: "intel_failed_001",
          version: 1,
          is_current: true,
          languages: {},
          frameworks: [],
          package_managers: [],
          entry_points: [],
          structure: {},
          commit_style: {},
          visual_tokens: {},
          harvested_at: "2026-04-24T00:00:00Z",
          harvested_by: "worker",
          harvest_duration_ms: 1234,
          harvest_error: "synthetic test failure",
        },
      });
      return;
    }
    await fulfillJson(route, 200, {
      intel: {
        intel_id: "intel_ready_001",
        version: 1,
        is_current: true,
        languages: { typescript: 0.7, python: 0.3 },
        frameworks: ["next.js"],
        package_managers: ["npm"],
        entry_points: [],
        structure: { file_count: 42, depth_p50: 3, top_level_dirs: [] },
        commit_style: {},
        visual_tokens: {},
        harvested_at: "2026-04-24T00:00:00Z",
        harvested_by: "worker",
        harvest_duration_ms: 1234,
        harvest_error: null,
      },
    });
  });
}

/**
 * Stub the manual-retry POST so the badge's "Retry harvest" button
 * doesn't 404 against a non-existent backend during e2e runs. We
 * don't assert on the response body; the badge re-enters the
 * ``harvesting`` state on success and that's enough to verify.
 */
export async function mockIntelHarvest(page: Page): Promise<void> {
  await page.route("**/api/onboard/intel-harvest", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await fulfillJson(route, 200, {
      handle: { enqueued: true, job_id: "arq:job:retry", intel_id: null },
    });
  });
}

/**
 * Convenience: install all client-side mocks the done page needs in
 * one call. Pass per-mock overrides for tests that want to assert
 * specific states (e.g. seed-result fallback fixture).
 */
export async function mockDonePageRoutes(
  page: Page,
  opts: {
    seedLatest?: WizardSeedResult | null;
    intelCurrent?: "missing" | "ready" | "failed";
  } = {},
): Promise<void> {
  await mockWizardSeedLatest(page, opts.seedLatest ?? null);
  await mockIntelCurrent(page, opts.intelCurrent ?? "missing");
  await mockIntelHarvest(page);
}

// ---------------------------------------------------------------------------
// sessionStorage seeding
// ---------------------------------------------------------------------------

/**
 * Seed ``ship.wizard_seed_result.<repoId>`` so the Done step's
 * client component renders the result card without falling back to
 * the API. Mirrors the write the Confirm step (P5-08) performs after
 * a successful wizard_seed POST. Must be installed via
 * :func:`Page.addInitScript` so it lands BEFORE Next's hydration.
 */
export async function seedWizardResultInSession(
  page: Page,
  repoId: string,
  result: WizardSeedResult,
): Promise<void> {
  await page.addInitScript(
    ([key, payload]) => {
      try {
        window.sessionStorage.setItem(key, payload);
      } catch {
        // Private mode / storage disabled — tests that exercise the
        // sessionStorage path must run in a normal browser context.
      }
    },
    [`ship.wizard_seed_result.${repoId}`, JSON.stringify(result)] as const,
  );
}
