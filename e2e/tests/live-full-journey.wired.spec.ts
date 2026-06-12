import { expect, test } from "@playwright/test";

import { GH_API, ghHeaders, parseRepo } from "../lib/github-rest";
import {
  hasShipApiCredentials,
  shipApiBase,
  shipApiGet,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * Live staging aggregate check.
 *
 * The detailed side-effect flows stay in their own specs. This spec stitches
 * their expected end-state together: deployed API is healthy, the authenticated
 * console renders the core product surfaces, the sandbox repo is wired, and the
 * process/dashboard APIs respond for the same workspace.
 *
 * @deployed
 */
test.describe("live staging full journey aggregate (wired)", () => {
  test.describe.configure({ mode: "serial", timeout: 180_000 });

  test("@deployed live workspace is fully operable", async ({ page, request }) => {
    test.skip(
      process.env.E2E_RUN_LIVE_FULL_JOURNEY !== "1",
      "Set E2E_RUN_LIVE_FULL_JOURNEY=1 to run the live aggregate",
    );
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (authenticated console session)",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );

    await test.step("Ship API is healthy", async () => {
      const health = await request.get(`${shipApiBase()}/v1/health`);
      expect(health.ok(), `GET /v1/health ${health.status()}`).toBeTruthy();
      const body = (await health.json()) as { status?: string; database?: string };
      expect(body.status).toBeTruthy();
      expect(body.database).toBeTruthy();
    });

    const workspaceId = await shipResolveWorkspaceId(request);
    const ws = encodeURIComponent(workspaceId);

    await test.step("workspace API surfaces are coherent", async () => {
      const endpoints = [
        "/pipelines",
        "/dashboard",
        "/clarifications",
        "/improvements",
        "/processes",
        "/integrations",
        "/repos",
      ] as const;
      for (const endpoint of endpoints) {
        const res = await shipApiGet(request, `/v1/workspaces/${ws}${endpoint}`);
        expect(
          res.ok(),
          `GET /v1/workspaces/{ws}${endpoint} ${res.status()}`,
        ).toBeTruthy();
      }

      const processes = await shipApiGet(request, `/v1/workspaces/${ws}/processes`);
      const processBody = (await processes.json()) as {
        primary_process_id?: string | null;
        processes?: unknown[];
      };
      expect(Array.isArray(processBody.processes)).toBeTruthy();
      expect(processBody.processes?.length ?? 0).toBeGreaterThan(0);
      expect(processBody.primary_process_id).toBeTruthy();
    });

    await test.step("authenticated console surfaces render", async () => {
      const surfaces: [string, RegExp][] = [
        ["/", /Workspace home/i],
        ["/process", /Workspace Map/i],
        ["/inbox", /^Inbox$/i],
        ["/knowledge", /Knowledge buckets/i],
        ["/integrations", /^Integrations$/i],
        ["/members", /^Members$/i],
      ];
      for (const [path, heading] of surfaces) {
        await page.goto(path);
        await expect(
          page.getByRole("heading", { name: heading }).first(),
          `heading on ${path}`,
        ).toBeVisible({ timeout: 30_000 });
      }
    });

    const sandbox = process.env.E2E_SANDBOX_REPO?.trim();
    const ghToken = process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN;
    if (sandbox && ghToken) {
      await test.step("GitHub sandbox repo is reachable", async () => {
        const parsed = parseRepo(sandbox);
        expect(parsed, `E2E_SANDBOX_REPO=${sandbox}`).not.toBeNull();
        const repo = await request.get(
          `${GH_API}/repos/${parsed!.owner}/${parsed!.repo}`,
          { headers: ghHeaders(ghToken) },
        );
        expect(repo.ok(), `GitHub repo ${repo.status()}`).toBeTruthy();
      });
    }
  });
});
