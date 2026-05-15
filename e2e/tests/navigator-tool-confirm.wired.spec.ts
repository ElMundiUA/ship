/**
 * Multi-turn confirmation flows for admin-mutating tools.
 *
 * Navigator gates mutating tools (``inbox_dispose``, ``project_update``,
 * ``ticket_create``, ``config_put`` …) behind ``ship-choice``
 * confirmation — turn 1 the agent describes the intended change +
 * waits, turn 2 the user explicitly confirms and the tool fires.
 *
 * Each test here issues both turns on the same thread (no
 * archive between them via the new ``streamTwoTurnTurn`` helper)
 * and asserts the matching tool actually invoked on turn 2.
 *
 * Same retry-after-failure invariant: across both turns combined,
 * if the agent gets ``ok=false`` from a tool and immediately tries
 * a different one, the test fails with the punch-list.
 */

import { expect, test } from "@playwright/test";

import {
  hasMemorySuiteCredentials,
  memorySuiteEnv,
  type AuthCtx,
} from "../lib/memory-helpers";
import {
  analyseToolTrajectory,
  streamTwoTurnTurn,
  type ToolTrajectoryAnalysis,
} from "../lib/navigator-sse";


function ctxOrThrow(): AuthCtx {
  const env = memorySuiteEnv();
  return {
    base: env.base!,
    token: env.primaryPat!,
    workspaceId: env.workspaceId!,
  };
}


function annotate(
  testInfo: { annotations: Array<{ type: string; description?: string }> },
  analysis: ToolTrajectoryAnalysis,
): void {
  const ran = analysis.invocations
    .map((i) => `${i.name}${i.ok ? "✓" : "✗"}`)
    .join(", ");
  testInfo.annotations.push({
    type: "tool-trajectory",
    description: ran || "(no tools fired)",
  });
}


function assertNoRetryAfterFailure(analysis: ToolTrajectoryAnalysis): void {
  if (analysis.retryAfterFailure.length === 0) return;
  const lines = analysis.retryAfterFailure
    .map(
      ({ failed, retried }) =>
        `  • ${failed.name} → ${failed.error ?? "?"}; agent then called ${retried.name}`,
    )
    .join("\n");
  throw new Error(
    "Navigator retried after a tool failure across confirmation turns:\n" +
      lines,
  );
}


function expectAnyOf(
  testInfo: { annotations: Array<{ type: string; description?: string }> },
  analysis: ToolTrajectoryAnalysis,
  expected: ReadonlySet<string>,
  label: string,
): void {
  const fired = analysis.invocations.map((i) => i.name);
  const hit = fired.some((n) => expected.has(n));
  if (!hit) {
    testInfo.annotations.push({
      type: "confirm-skipped",
      description: `${label}: expected one of ${[...expected].join("/")}, got ${fired.join(", ") || "(none)"}`,
    });
  }
  expect(
    hit,
    `${label}: expected ${[...expected].join("/")}, got ${fired.join(", ") || "(none)"}`,
  ).toBe(true);
}


test.describe("navigator tool confirmation flows", () => {
  test.describe.configure({ mode: "serial", timeout: 4 * 60_000 });

  test.beforeEach(() => {
    test.skip(
      process.env.E2E_RUN_NAVIGATOR_STREAM !== "1",
      "Set E2E_RUN_NAVIGATOR_STREAM=1 to burn LLM tokens",
    );
    test.skip(
      !hasMemorySuiteCredentials(),
      "Set E2E_NAVIGATOR_WORKSPACE_ID + E2E_NAVIGATOR_PAT_PRIMARY + E2E_SHIP_API_BASE",
    );
  });

  test("ticket_create after confirmation", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamTwoTurnTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "I want a new ticket: title 'Add cache invalidation on rerank rebuild', " +
        "body 'Wire the cache invalidator into the rerank rebuild pipeline so " +
        "stale results don't survive a model swap.' Just confirm it lines up " +
        "with the rerank plan, then create it.",
      followup:
        "ship-choice: yes, create the ticket exactly as drafted. Proceed.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    expectAnyOf(
      testInfo,
      analysis,
      new Set(["ticket_create"]),
      "ticket_create",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("ticket_update after confirmation", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamTwoTurnTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Update ticket MEM-2 — change its priority to high and add a note " +
        "about needing PO sign-off before exec proceeds. Wait for me to " +
        "confirm before writing.",
      followup:
        "ship-choice: confirmed, apply the priority + note update on MEM-2.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    expectAnyOf(
      testInfo,
      analysis,
      new Set(["ticket_update"]),
      "ticket_update",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("project_update after confirmation", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamTwoTurnTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      // Prompt picks a section the seeded body genuinely lacks
      // (``## Timeline``). Earlier wording asked for ``## Risks``
      // and the model kept hallucinating that the section already
      // existed, refusing to write — an LLM confusion between
      // prompt content and project state, not a tool/prompt bug.
      // The unambiguous ask makes the test pin the actual write
      // path rather than the model's "is this already there?"
      // pre-check.
      body:
        "Update the 'memory-search-overhaul' project — append a new " +
        "'## Timeline' section saying we target shipping the dense " +
        "reranker by 2026-Q3 and the cache rebuild by 2026-Q4. " +
        "Wait for explicit go-ahead before writing.",
      followup:
        "ship-choice: yes, append that Timeline section to the project body.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    expectAnyOf(
      testInfo,
      analysis,
      new Set(["project_update"]),
      "project_update",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("inbox_dispose after confirmation", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamTwoTurnTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "I want to resolve the inbox item titled 'Pending clarification on " +
        "rerank thresholds' as answered (we settled on 0.30). Wait for me " +
        "to confirm.",
      followup:
        "ship-choice: yes, dispose that clarification as answered.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    expectAnyOf(
      testInfo,
      analysis,
      new Set(["inbox_dispose", "inbox_update"]),
      "inbox_dispose",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("config_put after confirmation", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamTwoTurnTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Switch the workspace's default agent profile to ``solo`` — I want " +
        "everything to land on the smaller model. Wait for ship-choice " +
        "before writing.",
      followup:
        "ship-choice: yes, apply default_agent_profile=solo at the workspace level.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    expectAnyOf(
      testInfo,
      analysis,
      new Set(["config_put"]),
      "config_put",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });
});
