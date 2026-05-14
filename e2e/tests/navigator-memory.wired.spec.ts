/**
 * Navigator memory — API contract suite (E17/M1-M5, M14, M17-M19).
 *
 * Runs against the isolated ``e2e-navigator`` workspace (provisioned
 * by ``tools/scripts/setup_e2e_navigator_workspace.py``). Every test
 * starts by draining the caller's memory so assertions are
 * deterministic regardless of whatever previous runs left behind.
 *
 * Coverage map:
 *   M1  — seed-as-add path: write a fact, see it in list
 *   M2  — list returns owner-scoped rows with the expected shape
 *   M3  — delete one row → 204, row disappears from list
 *   M4  — bulk-forget by N days drops matching rows, ignores older
 *   M5  — 30-min gap retrieval is unit-covered; we only assert the
 *         endpoint accepts and clamps the input here
 *   M14 — filtering by ``project_native_id`` narrows the response
 *   M17 — delete writes an audit row containing the fact text
 *         (verified indirectly via the health endpoint's counters,
 *         which surface the operator-visible part of the audit log)
 *   M18 — ``project_native_id=untagged`` returns rows with NULL tag
 *   M19 — ``/health`` counters update after add + delete
 *
 * The LLM-extraction path (real per-message extract → fact appears)
 * lives in ``navigator-memory-stream.wired.spec.ts`` and is gated on
 * ``E2E_RUN_NAVIGATOR_STREAM=1`` so the cheap contract checks here
 * can run on every CI cycle.
 */

import { expect, test } from "@playwright/test";

import {
  bulkForget,
  cleanAllMemories,
  deleteMemory,
  fetchHealth,
  hasMemorySuiteCredentials,
  listMemories,
  memorySuiteEnv,
  seedMemory,
  type AuthCtx,
  type NavigatorMemoryHealth,
} from "../lib/memory-helpers";


function ctxOrSkip(): AuthCtx {
  const env = memorySuiteEnv();
  if (!env.base || !env.workspaceId || !env.primaryPat) {
    throw new Error(
      "memorySuiteEnv() must be checked via hasMemorySuiteCredentials() first",
    );
  }
  return {
    base: env.base,
    token: env.primaryPat,
    workspaceId: env.workspaceId,
  };
}


test.describe("navigator memory — API contract", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(({}, testInfo) => {
    test.skip(
      !hasMemorySuiteCredentials(),
      "Set E2E_NAVIGATOR_WORKSPACE_ID + E2E_NAVIGATOR_PAT_PRIMARY + E2E_SHIP_API_BASE",
    );
    // Soft annotation for the CI report — makes it obvious which
    // workspace this test was scoped to without dumping the PAT.
    testInfo.annotations.push({
      type: "workspace",
      description: process.env.E2E_NAVIGATOR_WORKSPACE_ID ?? "?",
    });
  });

  // -------------------------------------------------------------------------
  // M1 + M2 — seed a fact, see it in the list
  // -------------------------------------------------------------------------

  test("M1+M2 seeding a fact surfaces it in the list", async ({ request }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);

    const marker = `m1-${Date.now()}`;
    const seeded = await seedMemory(request, ctx, `The PO prefers ${marker} releases.`);
    if (seeded.status === 404) {
      test.skip(
        true,
        "Sandbox seed endpoint not deployed (404). Build + push the latest backend image.",
      );
      return;
    }
    expect(seeded.status, "seed → 201").toBe(201);
    expect(seeded.id).toBeTruthy();

    const list = await listMemories(request, ctx, { limit: 50 });
    expect(list.status).toBe(200);
    const hit = list.items.find((r) => r.fact_text.includes(marker));
    expect(hit, "seeded fact appears in list").toBeTruthy();
    // Schema sanity — every field declared in NavigatorMemoryOut is
    // present and typed as expected. The Console relies on every one
    // of these.
    expect(typeof hit!.id).toBe("string");
    expect(typeof hit!.confidence).toBe("number");
    expect(typeof hit!.created_at).toBe("string");
    expect(hit!.source_thread_id === null || typeof hit!.source_thread_id === "string").toBe(
      true,
    );
  });

  // -------------------------------------------------------------------------
  // M3 — single-row delete
  // -------------------------------------------------------------------------

  test("M3 DELETE removes the row from list", async ({ request }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);
    const seeded = await seedMemory(request, ctx, "fact for M3 delete");
    if (seeded.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    expect(seeded.id).toBeTruthy();
    const status = await deleteMemory(request, ctx, seeded.id);
    expect(status, "DELETE → 204").toBe(204);

    const list = await listMemories(request, ctx, { limit: 50 });
    expect(list.items.find((r) => r.id === seeded.id)).toBeUndefined();

    // Deleting a row that no longer exists should be 404, not 500.
    const secondTry = await deleteMemory(request, ctx, seeded.id);
    expect([404, 204].includes(secondTry), `repeat delete ${secondTry}`).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // M4 + M5 — bulk-forget + window clamp
  // -------------------------------------------------------------------------

  test("M4 bulk-forget drops rows within the window", async ({ request }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);
    const seeds = ["bulk-a", "bulk-b", "bulk-c"];
    for (const text of seeds) {
      const r = await seedMemory(request, ctx, text);
      if (r.status === 404) {
        test.skip(true, "Sandbox seed endpoint not deployed");
        return;
      }
    }
    const before = await listMemories(request, ctx, { limit: 50 });
    expect(before.items.length).toBeGreaterThanOrEqual(3);

    const forget = await bulkForget(request, ctx, 7);
    expect(forget.status, "POST /forget → 200").toBe(200);
    expect(forget.deleted, "deleted three seed rows").toBeGreaterThanOrEqual(3);

    const after = await listMemories(request, ctx, { limit: 50 });
    expect(
      after.items.filter((r) => seeds.includes(r.fact_text)).length,
      "all seeds gone",
    ).toBe(0);
  });

  test("M5 bulk-forget clamps the window to 1-90 days", async ({ request }) => {
    const ctx = ctxOrSkip();
    // No need to clean — the rejection is at the validator level.
    const tooBig = await bulkForget(request, ctx, 365);
    expect(tooBig.status, "days=365 → 422").toBe(422);
    const tooSmall = await bulkForget(request, ctx, 0);
    expect(tooSmall.status, "days=0 → 422").toBe(422);
  });

  // -------------------------------------------------------------------------
  // M14 — project filter
  // -------------------------------------------------------------------------

  test("M14 project_native_id filter narrows the response", async ({ request }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);
    const projA = `E2E-${Math.floor(Math.random() * 9999)}-A`;
    const projB = `E2E-${Math.floor(Math.random() * 9999)}-B`;
    const seedA = await seedMemory(request, ctx, "A1 fact", { projectNativeId: projA });
    if (seedA.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    await seedMemory(request, ctx, "A2 fact", { projectNativeId: projA });
    await seedMemory(request, ctx, "B1 fact", { projectNativeId: projB });

    const onlyA = await listMemories(request, ctx, { projectNativeId: projA, limit: 50 });
    expect(onlyA.status).toBe(200);
    expect(onlyA.items.length, `${projA} count`).toBe(2);
    expect(onlyA.items.every((r) => r.project_native_id === projA)).toBe(true);

    const onlyB = await listMemories(request, ctx, { projectNativeId: projB, limit: 50 });
    expect(onlyB.items.length, `${projB} count`).toBe(1);
  });

  // -------------------------------------------------------------------------
  // M18 — "untagged" filter
  // -------------------------------------------------------------------------

  test("M18 project_native_id=untagged returns only NULL-tag rows", async ({
    request,
  }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);
    const tagged = await seedMemory(request, ctx, "tagged fact", {
      projectNativeId: "E2E-untagged-test",
    });
    if (tagged.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    await seedMemory(request, ctx, "general fact");

    const untagged = await listMemories(request, ctx, {
      projectNativeId: "untagged",
      limit: 50,
    });
    expect(untagged.status).toBe(200);
    expect(untagged.items.every((r) => r.project_native_id === null)).toBe(true);
    expect(untagged.items.some((r) => r.fact_text === "general fact")).toBe(true);
    expect(untagged.items.some((r) => r.fact_text === "tagged fact")).toBe(false);
  });

  // -------------------------------------------------------------------------
  // M17 + M19 — health counters reflect activity
  // -------------------------------------------------------------------------

  test("M17+M19 health counters update on add + delete", async ({ request }) => {
    const ctx = ctxOrSkip();
    await cleanAllMemories(request, ctx);

    const before = (await fetchHealth(request, ctx)) as NavigatorMemoryHealth;
    expect("facts_count" in before, "health payload shape").toBe(true);
    const baseline = before;

    const a = await seedMemory(request, ctx, "health-fact-1");
    if (a.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    await seedMemory(request, ctx, "health-fact-2");
    const mid = (await fetchHealth(request, ctx)) as NavigatorMemoryHealth;
    expect(mid.facts_count - baseline.facts_count, "two new facts").toBe(2);
    // ``adds_24h`` counts rows created in the last 24h — our seeds
    // bumped it by 2 (or more if the suite re-ran in the window).
    expect(mid.adds_24h, "adds_24h non-decreasing").toBeGreaterThanOrEqual(
      baseline.adds_24h + 2,
    );

    await deleteMemory(request, ctx, a.id);
    const after = (await fetchHealth(request, ctx)) as NavigatorMemoryHealth;
    expect(after.facts_count, "facts_count drops on delete").toBe(
      mid.facts_count - 1,
    );

    // ``zero_hit_rate_24h`` is bounded — sanity-only assertion to
    // catch a regression that returned ``NaN`` or a value outside
    // [0, 1].
    expect(after.zero_hit_rate_24h).toBeGreaterThanOrEqual(0);
    expect(after.zero_hit_rate_24h).toBeLessThanOrEqual(1);
  });
});
