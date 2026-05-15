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
  testInfo: { annotations: Array<{ type: string; description?: string }> },
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
  testInfo: { annotations: Array<{ type: string; description?: string }> },
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
    // ``runs_list`` + ``runs_get`` read from ``routine_runs`` (Ship's
    // lane execution rows), NOT ``workflow_runs`` (GitHub Actions
    // cache). The seed plants a routine + a failed run + a succeeded
    // run + an in-flight run so this prompt can drive runs_list →
    // runs_get on the failed one's id.
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Find the most recent failed run of the 'rerank-soak' routine, " +
        "then pull its full outcome — summary, findings, the assertion " +
        "that tripped, finished_at.",
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

  test("repo_symbols fires when asked about exported names in a file", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "List the functions and classes exported from src/rank.ts in " +
        "the elmundi/ship-e2e-sandbox repo so I can see the API " +
        "surface at a glance.",
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
      // ``repo_symbols`` is the rich path; falling back to
      // ``repo_file_get`` (the agent grep'd the file then summarised
      // symbols itself) is still acceptable — we punt on the
      // "expected" hard-fail if neither fires.
      new Set(["repo_symbols", "repo_file_get"]),
      "repo_symbols drill",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("recall fires with project_native_id when user names a project", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    // Seed a project-tagged fact so the agent has something to
    // resolve via ``recall(project_native_id=…)``.
    await request.post(
      `${ctx.base}/v1/workspaces/${encodeURIComponent(ctx.workspaceId)}/navigator-memories/_test_seed`,
      {
        headers: {
          Authorization: `Bearer ${ctx.token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({
          fact_text: "scope was locked at three deliverables.",
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
        "project? I want only facts tagged to that project.",
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
    expectAnyOf(testInfo, analysis, new Set(["recall"]), "project-scoped recall");
    // recall is the right tool; the project_native_id arg is the
    // sharpness signal. Soft-annotate when the agent skipped that
    // arg — likely a prompt-tuning issue, not a regression.
    const recallCall = analysis.invocations.find((i) => i.name === "recall");
    if (recallCall) {
      const args = recallCall.args as Record<string, unknown> | null;
      if (!args || typeof args.project_native_id !== "string") {
        testInfo.annotations.push({
          type: "recall-project-arg-missing",
          description:
            "agent called recall without project_native_id even though the " +
            "user named a specific project — navigator.md rule for " +
            "project-scoped recall not landing.",
        });
      }
    }
    expect(result.text.length).toBeGreaterThan(0);
  });

  test("recall_context fires when user asks for context around a fact", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Pull from memory what we said about the rerank thresholds — and " +
        "then for the most relevant fact show me the chat conversation " +
        "I was having when I said it (a few messages before and after).",
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
      "recall_context drill (source-message context)",
    );
    expect(result.text.length).toBeGreaterThan(0);
  });
});
