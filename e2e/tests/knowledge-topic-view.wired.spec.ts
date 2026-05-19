/**
 * Wired e2e — Navigator topic view detail at `/knowledge/topics/{topic_tag}`.
 *
 * Locks SSR rendering of rendered canon (markdown article + claims aside)
 * and the documented empty state for unknown tags.
 *
 * Seeding (no public POST for topic views):
 *   1. `GET /v1/workspaces/{ws}/knowledge/topic-views?limit=1` — first row
 *      supplies `topic_tag` for the happy path when the workspace has canon.
 *   2. Optional `E2E_KNOWLEDGE_TOPIC_TAG` — overrides the probe (dogfood /
 *      staging with a known tag).
 *   3. Negative path uses `e2e-no-view-{random}` (guaranteed 404).
 *
 * Topic views are derived: renderer cron + ≥3 active claims per tag
 * (`knowledge_topic_renderer._MIN_CLAIMS_PER_TOPIC`). Blank workspaces skip
 * the happy path with an explicit message; TV-02 (unknown tag) always runs.
 *
 * Requires:
 *   - E2E_STORAGE_STATE — signed-in console session.
 *   - E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN — PAT for API oracle + probe.
 *   - Optional E2E_WORKSPACE_ID, E2E_KNOWLEDGE_TOPIC_TAG.
 */

import { expect, test } from "@playwright/test";

import {
  hasShipApiCredentials,
  shipApiGet,
  shipResolveWorkspaceId,
} from "../lib/ship-api";
import { hasPlaywrightStorageState } from "../lib/storage";

type ApiTopicViewSummary = {
  topic_tag: string;
  title: string;
};

type ApiTopicViewDetail = {
  topic_tag: string;
  title: string;
  body_md: string;
  claim_count: number;
  rendered_by_model: string | null;
  claims: { claim_md: string }[];
};

/** Mirrors `stripLeadingH1` on the topic page (duplicate H1 in body). */
function stripLeadingH1(body: string): string {
  const lines = (body || "").split("\n");
  let cursor = 0;
  while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  if (cursor < lines.length && /^#\s+/.test(lines[cursor])) {
    cursor++;
    while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  }
  return lines.slice(cursor).join("\n");
}

/** Plain substring from claim text (strip lightweight markdown). */
function plainClaimSnippet(claimMd: string): string {
  const plain = claimMd
    .replace(/[*_`[\]()]/g, "")
    .replace(/^[-*]\s+/, "")
    .trim();
  return plain.length >= 8 ? plain.slice(0, 120) : plain;
}

/** First prose substring from stripped markdown for DOM assertions. */
function firstProseSnippet(bodyMd: string): string {
  const stripped = stripLeadingH1(bodyMd);
  for (const line of stripped.split("\n")) {
    const t = line.trim();
    if (!t || /^#+\s/.test(t)) continue;
    const plain = t
      .replace(/^[-*]\s+/, "")
      .replace(/[*_`[\]()]/g, "")
      .trim();
    if (plain.length >= 8) return plain.slice(0, 120);
  }
  const fallback = stripped.replace(/[#*_`\[\]()]/g, "").trim();
  return fallback.slice(0, 120) || "rendered";
}

async function resolveTopicTag(
  request: import("@playwright/test").APIRequestContext,
  workspaceId: string,
): Promise<string | null> {
  const pinned = process.env.E2E_KNOWLEDGE_TOPIC_TAG?.trim();
  if (pinned) return pinned;

  const list = await shipApiGet(
    request,
    `/v1/workspaces/${workspaceId}/knowledge/topic-views?limit=1`,
  );
  if (!list.ok()) {
    throw new Error(`GET topic-views → ${list.status()}`);
  }
  const rows = (await list.json()) as ApiTopicViewSummary[];
  return rows[0]?.topic_tag ?? null;
}

async function fetchTopicDetail(
  request: import("@playwright/test").APIRequestContext,
  workspaceId: string,
  topicTag: string,
): Promise<ApiTopicViewDetail> {
  const res = await shipApiGet(
    request,
    `/v1/workspaces/${workspaceId}/knowledge/topic-views/${encodeURIComponent(topicTag)}`,
  );
  expect(res.ok(), `GET topic-views/${topicTag}`).toBeTruthy();
  return (await res.json()) as ApiTopicViewDetail;
}

async function assertLiveTopicView(
  page: import("@playwright/test").Page,
  detail: ApiTopicViewDetail,
): Promise<void> {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(detail.title);

  const article = page.getByTestId("topic-view-article");
  await expect(article).toBeVisible({ timeout: 30_000 });
  const snippet = firstProseSnippet(detail.body_md);
  await expect(article).toContainText(snippet, { timeout: 15_000 });

  const claims = page.getByTestId("topic-view-claims");
  await expect(claims).toBeVisible();
  await expect(claims).toContainText(`Claims · ${detail.claims.length}`);
  if (detail.claims[0]?.claim_md) {
    const claimSnippet = plainClaimSnippet(detail.claims[0].claim_md);
    if (claimSnippet.length >= 8) {
      await expect(claims).toContainText(claimSnippet);
    }
  }

  if (detail.rendered_by_model === "deterministic") {
    await expect(page.getByText("deterministic fallback")).toBeVisible();
  }
}

test.describe("knowledge topic view (wired)", () => {
  test.beforeEach(() => {
    test.skip(
      !hasPlaywrightStorageState(),
      "Set E2E_STORAGE_STATE (see e2e/README.md)",
    );
    test.skip(
      !hasShipApiCredentials(),
      "Set E2E_SHIP_API_BASE + E2E_SHIP_API_TOKEN",
    );
  });

  test("unknown tag shows empty state copy", async ({ page }) => {
    const unknownTag = `e2e-no-view-${Date.now().toString(36)}`;
    await page.goto(`/knowledge/topics/${encodeURIComponent(unknownTag)}`);

    const empty = page.getByTestId("topic-view-empty");
    await expect(empty).toBeVisible({ timeout: 30_000 });
    await expect(empty).toContainText(
      "No topic view rendered for this tag yet",
    );
    await expect(page.getByTestId("topic-view-unavailable")).toHaveCount(0);
    await expect(page.getByTestId("topic-view-article")).toHaveCount(0);
  });

  test("direct URL shows title, article body, and claims from API", async ({
    page,
    request,
  }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    const topicTag = await resolveTopicTag(request, workspaceId);
    if (!topicTag) {
      test.skip(
        true,
        "No topic views in workspace — set E2E_KNOWLEDGE_TOPIC_TAG or wait for renderer",
      );
      return;
    }

    const detail = await fetchTopicDetail(request, workspaceId, topicTag);
    await page.goto(`/knowledge/topics/${encodeURIComponent(topicTag)}`);
    await assertLiveTopicView(page, detail);

    const pill = page.getByTestId("scope-pill");
    await expect(pill).toBeVisible();
    await expect(pill).toHaveAttribute("data-scope", "workspace");
  });

  test("click-through from /knowledge preserves workspace scope", async ({
    page,
    request,
  }) => {
    const workspaceId = await shipResolveWorkspaceId(request);
    const topicTag = await resolveTopicTag(request, workspaceId);
    if (!topicTag) {
      test.skip(
        true,
        "No topic views in workspace — set E2E_KNOWLEDGE_TOPIC_TAG or wait for renderer",
      );
      return;
    }

    const detail = await fetchTopicDetail(request, workspaceId, topicTag);

    await page.goto("/knowledge");
    const topicLink = page.locator(`a[href="/knowledge/topics/${encodeURIComponent(topicTag)}"]`).first();
    const linkCount = await topicLink.count();
    test.skip(
      linkCount === 0,
      "Knowledge grid has no link for probed topic — set E2E_KNOWLEDGE_TOPIC_TAG to a listed tag",
    );

    await topicLink.click();
    await expect(page).toHaveURL(
      new RegExp(`/knowledge/topics/${encodeURIComponent(topicTag).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`),
    );

    const pill = page.getByTestId("scope-pill");
    await expect(pill).toHaveAttribute("data-scope", "workspace");
    await assertLiveTopicView(page, detail);
  });
});
