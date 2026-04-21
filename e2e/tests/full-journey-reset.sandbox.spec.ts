import { expect, test } from "@playwright/test";

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

/**
 * Idempotent teardown for the full-journey sandbox. Runs **only** when
 * `E2E_RESET_SANDBOX=1` so routine CI doesn't mutate state by accident.
 *
 * Rolls back two surfaces:
 *   - GitHub `E2E_SANDBOX_REPO` — closes `[e2e]` issues + Ship-authored PRs,
 *     deletes `ship/*` branches (opt-in via `E2E_RESET_DELETE_BRANCHES=1`).
 *   - Ship workspace — disconnects activated repos + removes the tracker
 *     integration, so a subsequent run of `full-journey.wired.spec.ts`
 *     starts from a clean "step=repos" state (or "step=github" if the App
 *     install was also reverted out-of-band).
 *
 * This is the **sandbox** (API-only) spec project — no browser session, no
 * `E2E_STORAGE_STATE` required. Needs:
 *   - E2E_SANDBOX_REPO
 *   - GITHUB_TOKEN | E2E_GITHUB_TOKEN (repo admin; contents+issues+pulls write)
 *   - E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (workspace admin)
 */

test.describe("full-journey reset (sandbox)", () => {
  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RESET_SANDBOX !== "1",
      "Set E2E_RESET_SANDBOX=1 to run the destructive reset",
    );
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/"),
      "Set E2E_SANDBOX_REPO=owner/name",
    );
    test.skip(
      !(process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN),
      "Set GITHUB_TOKEN (repo write access)",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)",
    );
  });

  test("reset GitHub sandbox + Ship workspace", async ({ request }) => {
    const repo = process.env.E2E_SANDBOX_REPO!;
    const ghToken = (process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN)!;

    const gh = await resetGithubSandboxRepo(request, {
      repo,
      token: ghToken,
      deleteBranches: process.env.E2E_RESET_DELETE_BRANCHES === "1",
    });
    test.info().annotations.push({
      type: "github-reset",
      description: JSON.stringify(gh),
    });
    expect(
      gh.errors,
      `GitHub reset errors: ${gh.errors.join("; ")}`,
    ).toEqual([]);

    const wsId = await shipResolveWorkspaceId(request);
    const ship = await resetShipWorkspace(request, {
      base: shipApiBase()!,
      token: shipApiToken()!,
      workspaceId: wsId,
      // Narrow to the sandbox so shared workspaces don't lose their
      // production repo. Opt out with E2E_RESET_ALL_REPOS=1 when the
      // workspace is truly dedicated to e2e.
      onlyRepoFullName:
        process.env.E2E_RESET_ALL_REPOS === "1" ? undefined : repo,
    });
    test.info().annotations.push({
      type: "ship-reset",
      description: JSON.stringify(ship),
    });
    expect(
      ship.errors,
      `Ship reset errors: ${ship.errors.join("; ")}`,
    ).toEqual([]);
  });
});
