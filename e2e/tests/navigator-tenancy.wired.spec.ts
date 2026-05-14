/**
 * Navigator memory — cross-user tenancy isolation (E17/M10).
 *
 * Personal scope is the load-bearing claim behind the Console
 * ``/memory`` page: workspace admins must NOT see another user's
 * Navigator facts. The promise is enforced by the
 * ``WHERE owner_user_id = current_user.id`` filter in
 * ``apps/backend/app/services/agent/memory.py:list_for_user`` —
 * but the only way to prove that filter is correctly wired through
 * the REST surface is to drive it with two different PATs in the
 * same workspace and assert that user B never sees user A's rows.
 *
 * ``E2E_NAVIGATOR_PAT_PRIMARY`` and ``E2E_NAVIGATOR_PAT_SECONDARY``
 * are minted by ``tools/scripts/setup_e2e_navigator_workspace.py``;
 * both belong to the same ``e2e-navigator`` workspace, both have
 * ``role=admin`` so neither lacks privilege — the only thing that
 * separates them is ``users.id``.
 */

import { expect, test } from "@playwright/test";

import {
  cleanAllMemories,
  hasMemorySuiteCredentials,
  listMemories,
  memorySuiteEnv,
  seedMemory,
  type AuthCtx,
} from "../lib/memory-helpers";


test.describe("navigator memory — tenancy isolation", () => {
  // See navigator-memory.wired.spec.ts — cleanup of accumulated rows
  // pushes us past the default 30s timeout. 90s gives us headroom.
  test.describe.configure({ mode: "serial", timeout: 90_000 });

  test.beforeEach(() => {
    const env = memorySuiteEnv();
    test.skip(
      !hasMemorySuiteCredentials() || !env.secondaryPat,
      "Set E2E_NAVIGATOR_PAT_PRIMARY + E2E_NAVIGATOR_PAT_SECONDARY",
    );
  });

  test("M10 user B (workspace admin) cannot see user A's facts", async ({
    request,
  }) => {
    const env = memorySuiteEnv();
    const ctxA: AuthCtx = {
      base: env.base!,
      token: env.primaryPat!,
      workspaceId: env.workspaceId!,
    };
    const ctxB: AuthCtx = {
      base: env.base!,
      token: env.secondaryPat!,
      workspaceId: env.workspaceId!,
    };

    // Wipe both sides so the assertion is clean.
    await cleanAllMemories(request, ctxA);
    await cleanAllMemories(request, ctxB);

    const markerA = `tenancyA-${Date.now()}`;
    const markerB = `tenancyB-${Date.now()}`;
    const a = await seedMemory(request, ctxA, `Only A should see ${markerA}.`);
    if (a.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    const b = await seedMemory(request, ctxB, `Only B should see ${markerB}.`);
    expect(b.status).toBe(201);

    const listA = await listMemories(request, ctxA, { limit: 50 });
    expect(listA.items.some((r) => r.fact_text.includes(markerA))).toBe(true);
    expect(
      listA.items.some((r) => r.fact_text.includes(markerB)),
      "A must not see B's fact",
    ).toBe(false);

    const listB = await listMemories(request, ctxB, { limit: 50 });
    expect(listB.items.some((r) => r.fact_text.includes(markerB))).toBe(true);
    expect(
      listB.items.some((r) => r.fact_text.includes(markerA)),
      "B must not see A's fact",
    ).toBe(false);
  });

  test("M10b user B cannot delete user A's fact even by id", async ({
    request,
  }) => {
    const env = memorySuiteEnv();
    const ctxA: AuthCtx = {
      base: env.base!,
      token: env.primaryPat!,
      workspaceId: env.workspaceId!,
    };
    const ctxB: AuthCtx = {
      base: env.base!,
      token: env.secondaryPat!,
      workspaceId: env.workspaceId!,
    };
    await cleanAllMemories(request, ctxA);

    const seeded = await seedMemory(request, ctxA, "A's deletable fact");
    if (seeded.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    expect(seeded.id).toBeTruthy();

    // B tries to delete A's row by id. The server's owner-check
    // should refuse — ``memory.delete`` returns False, the route
    // raises 404 (rather than 403, so the existence of the row
    // doesn't leak across the tenancy boundary).
    const resp = await request.delete(
      `${env.base}/v1/workspaces/${encodeURIComponent(env.workspaceId!)}/navigator-memories/${encodeURIComponent(seeded.id)}`,
      {
        headers: { Authorization: `Bearer ${env.secondaryPat}` },
      },
    );
    expect(resp.status(), "B's DELETE on A's id").toBe(404);

    // Row must still be visible to A.
    const listA = await listMemories(request, ctxA, { limit: 50 });
    expect(listA.items.some((r) => r.id === seeded.id)).toBe(true);
  });

  test("M10c user B health endpoint does not count A's facts", async ({
    request,
  }) => {
    const env = memorySuiteEnv();
    const ctxA: AuthCtx = {
      base: env.base!,
      token: env.primaryPat!,
      workspaceId: env.workspaceId!,
    };
    const ctxB: AuthCtx = {
      base: env.base!,
      token: env.secondaryPat!,
      workspaceId: env.workspaceId!,
    };
    await cleanAllMemories(request, ctxA);
    await cleanAllMemories(request, ctxB);

    const seedA1 = await seedMemory(request, ctxA, "A1");
    if (seedA1.status === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    await seedMemory(request, ctxA, "A2");
    await seedMemory(request, ctxA, "A3");
    await seedMemory(request, ctxB, "B1");

    const respA = await request.get(
      `${env.base}/v1/workspaces/${encodeURIComponent(env.workspaceId!)}/navigator-memories/health`,
      { headers: { Authorization: `Bearer ${env.primaryPat}` } },
    );
    expect(respA.ok()).toBe(true);
    const healthA = (await respA.json()) as { facts_count: number };
    expect(healthA.facts_count, "A sees 3").toBe(3);

    const respB = await request.get(
      `${env.base}/v1/workspaces/${encodeURIComponent(env.workspaceId!)}/navigator-memories/health`,
      { headers: { Authorization: `Bearer ${env.secondaryPat}` } },
    );
    expect(respB.ok()).toBe(true);
    const healthB = (await respB.json()) as { facts_count: number };
    expect(healthB.facts_count, "B sees 1 (its own)").toBe(1);
  });
});
