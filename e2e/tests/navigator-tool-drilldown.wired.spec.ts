/**
 * Drill-in tool coverage.
 *
 * Each scenario hands the agent a specific id / slug / number so it
 * has no excuse to short-circuit through "answered from context".
 * The seed in ``tools/scripts/seed_e2e_navigator_state.py`` puts
 * concrete rows in the workspace; here we reference them by their
 * deterministic markers and assert the matching ``*_get`` tool
 * actually fires.
 *
 * Same retry-after-failure invariant as the parent suite — if the
 * agent ricochets between tools after a failure, the test punches a
 * line item out so the operator can grep straight to the bug.
 */

import { expect, test } from "@playwright/test";

import {
  hasMemorySuiteCredentials,
  memorySuiteEnv,
  type AuthCtx,
} from "../lib/memory-helpers";
import {
  analyseToolTrajectory,
  streamNavigatorTurn,
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
  testInfo: { annotations: Array<{ type: string; description: string }> },
  analysis: ToolTrajectoryAnalysis,
): void {
  const ran = analysis.invocations
    .map((i) => `${i.name}${i.ok ? "✓" : "✗"}`)
    .join(", ");
  testInfo.annotations.push({
    type: "tool-trajectory",
    description: ran || "(no tools fired — agent answered from context)",
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
    "Navigator retried after a tool failure (tool or prompt bug):\n" +
      lines,
  );
}


function expectAnyOf(
  testInfo: { annotations: Array<{ type: string; description: string }> },
  analysis: ToolTrajectoryAnalysis,
  expected: ReadonlySet<string>,
  label: string,
): void {
  const fired = analysis.invocations.map((i) => i.name);
  const hit = fired.some((n) => expected.has(n));
  if (!hit) {
    testInfo.annotations.push({
      type: "drill-skipped",
      description: `${label}: expected one of ${[...expected].join("/")}, got ${fired.join(", ") || "(none)"}`,
    });
  }
  expect(
    hit,
    `${label}: expected ${[...expected].join("/")}, got ${fired.join(", ") || "(none)"}`,
  ).toBe(true);
}


test.describe("navigator tool drill-in", () => {
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

  test("pr_get fires when the agent is asked about a specific PR number", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Pull the full details of pull request #1 in the " +
        "elmundi/ship-e2e-sandbox repo — title, state, head, base, body. " +
        "I'm reviewing it next.",
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
    expectAnyOf(testInfo, analysis, new Set(["pr_get", "pr_list"]), "pr_get drill");
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("repo_file_get fires when asked for a specific file path", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Show me the contents of README.md in the elmundi/ship-e2e-sandbox " +
        "repo on the main branch.",
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
    expectAnyOf(testInfo, analysis, new Set(["repo_file_get"]), "repo_file_get drill");
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("runs_get fires when asked about a specific run", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Find the failed CI run on the rerank-dense branch and pull " +
        "its full details — workflow, conclusion, logs link.",
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
      new Set(["runs_get", "runs_list"]),
      "runs_get drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("ticket_get fires when asked about a specific ticket id", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Read me the body and current state of ticket MEM-3 — the one " +
        "about picking the rerank algorithm.",
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
      new Set(["ticket_get", "ticket_list"]),
      "ticket_get drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("project_get fires when asked for the full project body", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Pull the full description and the linked tickets of the " +
        "'Memory & search overhaul' project.",
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
      new Set(["project_get", "project_list"]),
      "project_get drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("inbox_get fires when asked about a specific inbox title", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Look up the inbox item titled 'Pending clarification on rerank " +
        "thresholds' and show me its full details + history.",
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
      new Set(["inbox_get", "inbox_list"]),
      "inbox_get drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("recall_context fires on a project-tagged memory ask", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    // Seed a fact tagged with the project id first so recall_context
    // has a discriminator to filter on.
    await request.post(
      `${ctx.base}/v1/workspaces/${encodeURIComponent(ctx.workspaceId)}/navigator-memories/_test_seed`,
      {
        headers: {
          Authorization: `Bearer ${ctx.token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({
          fact_text: "memory-search-overhaul scope was locked at three deliverables.",
          project_native_id: "memory-search-overhaul",
          intent_at_capture: "shape_project",
        }),
      },
    );
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "What do we know specifically about the memory-search-overhaul " +
        "project? Pull project-tagged facts only.",
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
      new Set(["recall_context", "recall"]),
      "recall_context drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });
});
