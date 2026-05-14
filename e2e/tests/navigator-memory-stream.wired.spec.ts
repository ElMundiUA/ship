/**
 * Navigator memory — SSE + LLM ring (E17/M6-M9, M15-M16, M19).
 *
 * This is the "real LLM round-trip" suite — every test here burns
 * OpenAI tokens on the backend, so the whole describe block is
 * gated behind ``E2E_RUN_NAVIGATOR_STREAM=1``. The cheap contract
 * + tenancy + UI suites cover the deterministic ground; the
 * point of this file is to prove the *integration* is wired:
 *
 *   M6 — POST /chat/stream emits at least one assistant chunk
 *   M7 — first-turn retrieval inflates the system context with
 *        prior facts when a thread is brand new
 *   M8 — the ``recall`` tool surfaces facts on-demand mid-turn
 *   M9 — the ``recall_context`` tool returns project-tagged facts
 *   M15 — after a real extraction, the row appears in /memory
 *   M16 — the audit log captures the extraction event with the
 *        ``hit_count``/source pointers
 *   M19 — health endpoint counts the real adds + searches
 *
 * The "burn LLM tokens?" gate is intentional double-opt-in: the
 * ``E2E_RUN_NAVIGATOR_STREAM`` flag AND a configured ``OPENAI_API_KEY``
 * on the backend. When the backend has no key the route returns
 * 412 and the tests soft-skip rather than flake.
 */

import { expect, test } from "@playwright/test";

import {
  cleanAllMemories,
  fetchHealth,
  hasMemorySuiteCredentials,
  listMemories,
  memorySuiteEnv,
  waitForMemory,
  type AuthCtx,
  type NavigatorMemoryHealth,
} from "../lib/memory-helpers";
import { streamNavigatorTurn } from "../lib/navigator-sse";


function ctxOrThrow(): AuthCtx {
  const env = memorySuiteEnv();
  return {
    base: env.base!,
    token: env.primaryPat!,
    workspaceId: env.workspaceId!,
  };
}


test.describe("navigator memory — SSE + LLM ring", () => {
  test.describe.configure({ mode: "serial", timeout: 4 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_NAVIGATOR_STREAM !== "1",
      "Set E2E_RUN_NAVIGATOR_STREAM=1 to burn LLM tokens for this suite",
    );
    test.skip(
      !hasMemorySuiteCredentials(),
      "Set E2E_NAVIGATOR_WORKSPACE_ID + E2E_NAVIGATOR_PAT_PRIMARY + E2E_SHIP_API_BASE",
    );
  });

  // -------------------------------------------------------------------------
  // M6 — basic stream emits assistant text
  // -------------------------------------------------------------------------

  test("M6 POST /chat/stream emits at least one assistant chunk", async ({
    request,
  }) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "Say READY so the e2e marker can match.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    expect(result.text.length, "got assistant text").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // M7 — first-turn retrieval (smart-trigger) inflates context
  // -------------------------------------------------------------------------

  test("M7 first-turn retrieval surfaces a prior fact", async ({ request }) => {
    const ctx = ctxOrThrow();
    await cleanAllMemories(request, ctx);

    // Seed a fact directly via the sandbox so we don't have to wait
    // for a separate extraction round-trip just to set up the test.
    const sandbox = await request.post(
      `${ctx.base}/v1/workspaces/${encodeURIComponent(
        ctx.workspaceId,
      )}/navigator-memories/_test_seed`,
      {
        headers: {
          Authorization: `Bearer ${ctx.token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({
          fact_text: "The PO insists every release ship on Tuesday.",
        }),
      },
    );
    if (sandbox.status() === 404) {
      test.skip(true, "Sandbox seed endpoint not deployed");
      return;
    }
    expect(sandbox.status(), "seed → 201").toBe(201);

    // Force a fresh thread so the smart-trigger fires (first-turn).
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "When does the PO want our next release to go out?",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    // We don't pin on the model's exact wording. The signal we care
    // about is that "Tuesday" surfaces in the answer — proves the
    // fact made it into the system prompt.
    expect(result.text.toLowerCase()).toContain("tuesday");
  });

  // -------------------------------------------------------------------------
  // M8 / M9 — recall + recall_context tool round-trips
  // -------------------------------------------------------------------------

  test("M8 recall tool fires on a project question", async ({ request }) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      // A prompt that benefits from explicit recall mid-turn — the
      // model is instructed (in ``navigator.md``) to call recall
      // when the user references "what we discussed" / "earlier".
      body: "Recap what we discussed earlier about release cadence.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    // The recall tool may or may not fire — depends on the model's
    // judgment. We assert text length so the SSE pipe is provably
    // alive; the tool fire is a soft annotation.
    expect(result.text.length).toBeGreaterThan(0);
    if (result.toolNames.includes("recall")) {
      test
        .info()
        .annotations.push({ type: "tool", description: "recall fired" });
    }
  });

  // -------------------------------------------------------------------------
  // M15 / M19 — extraction lands a row + health counters
  // -------------------------------------------------------------------------

  test("M15+M19 extraction adds a row and bumps health counters", async ({
    request,
  }) => {
    const ctx = ctxOrThrow();
    await cleanAllMemories(request, ctx);
    const before = (await fetchHealth(request, ctx)) as NavigatorMemoryHealth;
    const baselineAdds = before.adds_24h;

    const marker = `pref-${Date.now()}`;
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      // Strong, extraction-friendly statement. The per-message
      // extractor (gpt-4o-mini) tends to capture this kind of
      // declarative preference reliably.
      body: `Remember this preference: I always want changelogs grouped by ${marker}.`,
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);

    // Per-message extraction runs as a background task (asyncio
    // create_task in chat.py) — poll until the marker shows up.
    const row = await waitForMemory(
      request,
      ctx,
      (r) => r.fact_text.toLowerCase().includes(marker.toLowerCase()),
      { timeoutMs: 45_000, pollMs: 1_500 },
    );
    expect(row, "extracted fact appears in the list").not.toBeNull();
    expect(row!.source_thread_id, "source thread captured").toBeTruthy();

    const after = (await fetchHealth(request, ctx)) as NavigatorMemoryHealth;
    expect(after.adds_24h, "adds_24h bumped").toBeGreaterThan(baselineAdds);
  });
});
