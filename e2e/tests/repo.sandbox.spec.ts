import { expect, test } from "@playwright/test";

/**
 * Validates the **test GitHub repo** after a human or bot completed onboarding.
 * Uses GITHUB_TOKEN (fine-grained: Contents read on that repo only).
 *
 * Env:
 *   E2E_SANDBOX_REPO — "owner/name"
 *   GITHUB_TOKEN or E2E_GITHUB_TOKEN
 */
test.describe("sandbox repo wiring (API)", () => {
  test.beforeEach(() => {
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/"),
      "Set E2E_SANDBOX_REPO=owner/name to run API checks",
    );
    test.skip(
      !(process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN),
      "Set GITHUB_TOKEN (or E2E_GITHUB_TOKEN) for GitHub REST reads",
    );
  });

  test(".ship/config.yml exists on default branch", async ({ request }) => {
    const repo = process.env.E2E_SANDBOX_REPO!;
    const token = process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN!;
    const res = await request.get(
      `https://api.github.com/repos/${repo}/contents/.ship/config.yml`,
      {
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
        },
      },
    );
    expect(res.ok(), `GitHub API ${res.status()}`).toBeTruthy();
    const body = await res.json();
    expect(body.type).toBe("file");
  });

  test("has at least one Ship workflow under .github/workflows", async ({
    request,
  }) => {
    const repo = process.env.E2E_SANDBOX_REPO!;
    const token = process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN!;
    const res = await request.get(
      `https://api.github.com/repos/${repo}/contents/.github/workflows`,
      {
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
        },
      });
    expect(res.ok(), `GitHub API ${res.status()}`).toBeTruthy();
    const items = await res.json();
    expect(Array.isArray(items)).toBeTruthy();
    const yml = (items as { name: string }[]).filter((f) =>
      f.name.endsWith(".yml"),
    );
    expect(yml.length).toBeGreaterThan(0);
  });
});
