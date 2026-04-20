import { expect, test } from "@playwright/test";

/**
 * GitHub REST: Actions и метки на **песочной репе** (трекер = GitHub Issues).
 * Дополняет repo.sandbox — проверяет, что CI жил и при желании есть лейбл clarification.
 *
 * Env (как в repo.sandbox):
 *   E2E_SANDBOX_REPO — owner/name
 *   GITHUB_TOKEN или E2E_GITHUB_TOKEN
 *
 * Опционально:
 *   E2E_EXPECT_SHIP_CLARIFICATION_LABEL=1 — assert label `ship:needs-clarification` exists
 */

function ghAuthHeaders() {
  const token = process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN;
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

test.describe("GitHub Actions + tracker labels (sandbox API)", () => {
  test.beforeEach(() => {
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/"),
      "Set E2E_SANDBOX_REPO=owner/name",
    );
    test.skip(
      !(process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN),
      "Set GITHUB_TOKEN",
    );
  });

  test("workflow runs endpoint returns a list structure", async ({
    request,
  }) => {
    const repo = process.env.E2E_SANDBOX_REPO!;
    const res = await request.get(
      `https://api.github.com/repos/${repo}/actions/runs?per_page=5`,
      { headers: ghAuthHeaders() },
    );
    expect(
      res.ok(),
      `GitHub Actions API ${res.status()} (needs actions:read on token)`,
    ).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body.workflow_runs)).toBeTruthy();
  });

  test("optionally: ship:needs-clarification label exists on repo", async ({
    request,
  }) => {
    test.skip(
      process.env.E2E_EXPECT_SHIP_CLARIFICATION_LABEL !== "1",
      "Set E2E_EXPECT_SHIP_CLARIFICATION_LABEL=1 to assert tracker label",
    );
    const repo = process.env.E2E_SANDBOX_REPO!;
    const res = await request.get(
      `https://api.github.com/repos/${repo}/labels`,
      { headers: ghAuthHeaders() },
    );
    expect(res.ok(), `labels ${res.status()}`).toBeTruthy();
    const labels = (await res.json()) as { name: string }[];
    expect(
      labels.some((l) => l.name === "ship:needs-clarification"),
    ).toBeTruthy();
  });
});
