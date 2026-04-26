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
  // The test body polls Ship for up to 120s waiting for the tracker
  // adapter to project the GitHub issue into a clarification row;
  // give the test harness enough budget to actually finish the poll
  // before Playwright's default 30s timeout fires.
  test.describe.configure({ timeout: 3 * 60_000 });
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

    // The sync endpoint is synchronous but the GitHub REST API can
    // briefly omit an issue that was just created in the same request
    // chain — new-row indexing propagates with a small lag. Instead
    // of calling ``/sync`` once and polling the DB, we re-sync on
    // each poll tick until the projection catches up.
    await expect
      .poll(
        async () => {
          const sync = await shipApiPost(
            request,
            `/v1/workspaces/${encodeURIComponent(ws)}/clarifications/sync`,
            {},
          );
          if (!sync.ok()) {
            // eslint-disable-next-line no-console
            console.log(
              `[tracker-e2e] POST /sync → ${sync.status()} ${await sync.text()}`,
            );
            return false;
          }
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
        { timeout: 120_000, intervals: [2_000, 3_000, 5_000] },
      )
      .toBe(true);

    if (hasPlaywrightStorageState()) {
      await page.goto("/inbox?type=clarification");
      await expect(
        page.getByRole("heading", { name: "Inbox" }),
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByText(marker, { exact: false }).first(),
      ).toBeVisible({ timeout: 30_000 });
    }
  });
});
