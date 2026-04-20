import { expect, test } from "@playwright/test";

import { GH_API, ghHeaders, parseRepo } from "../lib/github-rest";
import {
  hasShipApiCredentials,
  shipApiGet,
  shipApiPost,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

/**
 * GitHub Issue (лейбл `ship:needs-clarification`) + коммент с `@ship clarification:`
 * → `POST /v1/workspaces/{ws}/clarifications/sync` → строка в Clarifications.
 *
 * **Предусловия на dev:** та же репа, что в `E2E_SANDBOX_REPO`, активирована в
 * воркспейсе и для неё в Ship включён трекер GitHub Issues; Ship App установлен.
 *
 * @deployed
 */
test.describe("tracker: GitHub issue → clarifications projection", () => {
  test("@deployed sync + API poll + optional UI", async ({ page, request }) => {
    test.skip(
      !process.env.E2E_SANDBOX_REPO?.includes("/") ||
        !(process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN),
      "E2E_SANDBOX_REPO + GITHUB_TOKEN",
    );
    test.skip(
      !hasShipApiCredentials(),
      "E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN (admin)",
    );

    const full = process.env.E2E_SANDBOX_REPO!.trim();
    const parsed = parseRepo(full);
    expect(parsed, "owner/repo").toBeTruthy();
    const gh = process.env.GITHUB_TOKEN || process.env.E2E_GITHUB_TOKEN!;
    const headers = ghHeaders(gh);
    const marker = `e2e-tracker-${Date.now()}`;

    const lab = await request.post(
      `${GH_API}/repos/${parsed!.owner}/${parsed!.repo}/labels`,
      {
        headers,
        data: JSON.stringify({
          name: "ship:needs-clarification",
          color: "c5def5",
          description: "Ship clarification queue",
        }),
      },
    );
    if (!lab.ok() && lab.status() !== 422) {
      throw new Error(`create label → ${lab.status()}`);
    }

    const issueRes = await request.post(
      `${GH_API}/repos/${parsed!.owner}/${parsed!.repo}/issues`,
      {
        headers,
        data: JSON.stringify({
          title: `[e2e] ${marker}`,
          body: "Automated tracker→Ship projection test.",
          labels: ["ship:needs-clarification"],
        }),
      },
    );
    expect(issueRes.ok(), `create issue ${issueRes.status()}`).toBeTruthy();
    const issue = (await issueRes.json()) as { number: number };

    const commentBody =
      `> **@ship clarification:**\n` +
      `${marker} — what exactly should “done” mean for this slice?`;

    const comRes = await request.post(
      `${GH_API}/repos/${parsed!.owner}/${parsed!.repo}/issues/${issue.number}/comments`,
      {
        headers,
        data: JSON.stringify({ body: commentBody }),
      },
    );
    expect(comRes.ok(), `create comment ${comRes.status()}`).toBeTruthy();

    const ws = await shipResolveWorkspaceId(request);
    const sync = await shipApiPost(
      request,
      `/v1/workspaces/${encodeURIComponent(ws)}/clarifications/sync`,
      {},
    );
    expect(
      sync.ok(),
      `POST clarifications/sync → ${sync.status()} ${await sync.text()}`,
    ).toBeTruthy();

    await expect
      .poll(
        async () => {
          const r = await shipApiGet(
            request,
            `/v1/workspaces/${encodeURIComponent(ws)}/clarifications`,
          );
          if (!r.ok()) return false;
          const rows = (await r.json()) as { question: string; source: string }[];
          return rows.some(
            (x) => x.source === "tracker" && x.question.includes(marker),
          );
        },
        { timeout: 120_000 },
      )
      .toBe(true);

    if (hasPlaywrightStorageState()) {
      await page.goto("/clarifications");
      await expect(
        page.getByRole("heading", { name: "Clarifications" }),
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByText(marker, { exact: false }).first(),
      ).toBeVisible({ timeout: 30_000 });
    }
  });
});
