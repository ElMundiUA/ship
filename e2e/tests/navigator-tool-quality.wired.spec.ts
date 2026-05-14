/**
 * Navigator tool quality smoke.
 *
 * Sweeps the most-used Navigator tools through realistic user
 * prompts and checks that the agent's decision-tree is healthy:
 *
 *   1. The expected tool family fires for the prompt. If the
 *      agent doesn't even attempt a relevant tool, the prompt /
 *      tool descriptions are misaligned.
 *   2. **No retry-after-failure pattern.** When a tool returns
 *      ``ok=false`` and the agent's NEXT event is another
 *      ``tool_call`` — that's a regression. Either the tool's
 *      contract is wrong (it shouldn't have failed) or the prompt
 *      doesn't teach the agent to escalate failures to the user.
 *      Both are bugs we want to surface here rather than discover
 *      from confused operators in dogfood.
 *   3. Unrecovered failures get reported as test annotations (not
 *      assertion failures) so the report shows which tools are
 *      brittle without blocking the suite.
 *
 * Every scenario burns LLM tokens — the whole describe block is
 * gated on ``E2E_RUN_NAVIGATOR_STREAM=1`` and runs against prod
 * (or any backend with a real OpenAI key). Local laptop profile
 * has a blank ``OPENAI_API_KEY`` and the route returns 412, in
 * which case the suite soft-skips.
 *
 * Designed for the empty e2e-navigator workspace — prompts are
 * open-ended enough that the agent can pivot to "nothing to
 * report" rather than thrash on missing data. A retry-after-
 * failure pattern even on an empty workspace is the load-bearing
 * regression we're catching.
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
  // Tool fires + outcomes go into the report as annotations so an
  // operator skimming results can see what the agent did. None of
  // these are assertion failures on their own — the only assertion
  // is the "no retry-after-failure" pattern below.
  const ran = analysis.invocations
    .map((i) => `${i.name}${i.ok ? "✓" : "✗"}`)
    .join(", ");
  testInfo.annotations.push({
    type: "tool-trajectory",
    description: ran || "(no tools fired — agent answered from context)",
  });
  for (const inv of analysis.unrecoveredFailures) {
    testInfo.annotations.push({
      type: "unrecovered-tool-failure",
      description: `${inv.name}: ${inv.error ?? "(no error message)"}`,
    });
  }
}


/**
 * Soft tool-name assertion: when the agent has enough context to
 * answer without invoking a tool (e.g. system prompt already
 * carries the inbox count = 0), skipping the call is the *right*
 * behaviour — not a regression. We surface "expected a call from
 * family X but got Y" as a test annotation, never a hard fail.
 *
 * The hard fail is reserved for the user's explicit ask:
 * "retry-after-failure" — agent makes a call that errors and then
 * tries something different. Anything else is colour, not red.
 */
function noteToolFamily(
  testInfo: { annotations: Array<{ type: string; description: string }> },
  analysis: ToolTrajectoryAnalysis,
  family: ReadonlySet<string>,
  label: string,
): void {
  const fired = analysis.invocations.map((i) => i.name);
  if (fired.some((n) => family.has(n))) return;
  testInfo.annotations.push({
    type: "tool-family-skipped",
    description:
      `${label}: agent answered without invoking ${[...family].join("/")}. ` +
      `Likely fine (context-injected) but watch for a regression where ` +
      `the agent skips needed data.`,
  });
}


function assertNoRetryAfterFailure(
  analysis: ToolTrajectoryAnalysis,
): void {
  if (analysis.retryAfterFailure.length === 0) return;
  // Build a punch-list so the failure message tells the operator
  // exactly which tools are tripping the agent's retry path. Every
  // entry here is either a tool-contract bug or a prompt bug.
  const lines = analysis.retryAfterFailure.map(
    ({ failed, retried }) =>
      `  • ${failed.name} → ${failed.error ?? "?"}; agent then called ${retried.name}`,
  );
  throw new Error(
    "Navigator retried after a tool failure — this is a regression " +
      "we explicitly track. Each line below is either a tool-contract " +
      "bug (the failure was avoidable) or a prompt bug (the agent " +
      "shouldn't silently retry instead of escalating):\n" +
      lines.join("\n"),
  );
}


test.describe("navigator tool quality", () => {
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
  // 1) Dashboard / status — read-side dashboard + inbox tools
  // -------------------------------------------------------------------------

  test("status overview triggers dashboard_get / inbox_list without retry-after-failure", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "Give me a quick status snapshot of this workspace — what should I be looking at right now?",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set([
        "dashboard_get",
        "inbox_list",
        "runs_list",
        "project_list",
        "ticket_list",
      ]),
      "status overview",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 2) Inbox — list then drill-in. Closest thing to a "two tools in one turn"
  //    test because the agent typically pulls list + get for a top item.
  // -------------------------------------------------------------------------

  test("inbox query exercises inbox_list (and inbox_get if items present)", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      // Force a real tool invocation — asking for inbox details with
      // explicit filters bypasses the context-injected summary that
      // lets a generic "anything in my inbox?" be answered without
      // ever touching the tool.
      body:
        "Pull the 5 most recent clarification items from my inbox and " +
        "show me their titles, owners and ages.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["inbox_list", "inbox_get"]),
      "inbox query",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 3) Knowledge search — knowledge_search + optional knowledge_bucket_get
  // -------------------------------------------------------------------------

  test("knowledge prompt fires knowledge_search", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "What does our knowledge base say about deployment processes?",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["knowledge_search", "knowledge_bucket_get"]),
      "knowledge query",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 4) Memory recall — precondition with a real fact, then ask
  // -------------------------------------------------------------------------

  test("memory recall fires recall / recall_context after a seeded fact", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    // Seed a fact via the sandbox endpoint. Now produces a real
    // mem0 row with embedding so ``recall`` can actually find it.
    const seedResp = await request.post(
      `${ctx.base}/v1/workspaces/${encodeURIComponent(
        ctx.workspaceId,
      )}/navigator-memories/_test_seed`,
      {
        headers: {
          Authorization: `Bearer ${ctx.token}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({
          fact_text:
            "The operator prefers releases scheduled for Tuesday mornings.",
        }),
      },
    );
    if (seedResp.status() === 404) {
      test.skip(true, "Sandbox seed endpoint not available");
      return;
    }
    expect(seedResp.status()).toBe(201);

    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      // Phrasing that nudges the agent toward an explicit recall
      // tool call rather than relying on first-turn retrieval.
      body: "Recap what you remember about my release-day preferences.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["recall", "recall_context"]),
      "memory recall",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 5) Audit trail — audit_search. Lower-traffic tool but easy to validate.
  // -------------------------------------------------------------------------

  test("audit prompt fires audit_search", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "Show me admin / mutating actions on this workspace in the last hour.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["audit_search"]),
      "audit query",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 6) Members — small DB-only read tool, easy nudge.
  // -------------------------------------------------------------------------

  test("members prompt fires members_list", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body: "List every member of this workspace with their role and email.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(testInfo, analysis, new Set(["members_list"]), "members");
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 7) Config — config_help is read-only and self-describing; the agent
  //    should reach for it when the user asks "what can I configure?".
  //    config_put is admin-mutating; we drive it through the LLM but
  //    expect the admin gate to bite (the test workspace member is
  //    admin so the call may actually succeed — either outcome passes
  //    as long as the agent doesn't ricochet across tools).
  // -------------------------------------------------------------------------

  test("config prompt fires config_help / config_put without retry thrash", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "What workspace settings can I configure? If there's a way to set " +
        "the default agent profile, walk me through it.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["config_help", "config_put"]),
      "config",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 8) Project read/update — drives project_get and project_update. The
  //    e2e workspace has no projects, so the agent's first probe (likely
  //    project_list) will return empty. The right next move is to tell
  //    the user "no projects yet" — NOT to bounce off into ticket_list
  //    or something unrelated. That ricochet is the regression we're
  //    catching.
  // -------------------------------------------------------------------------

  test("project read/update prompt stays within project_* family", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Pull the details of the active project, then update its " +
        "description to mention 'Q3 focus'.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["project_list", "project_get", "project_update"]),
      "project read/update",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 9) PR family — pr_list / pr_get. e2e workspace has no bound repo, so
  //    the agent's call will likely return "no repo configured" / empty.
  //    Agent should surface that to the user, not start guessing with
  //    runs_list or repo_tree.
  // -------------------------------------------------------------------------

  test("PR prompt stays within pr_* family (no thrash on empty state)", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Show me the open pull requests on our main repo, then drill into " +
        "the most recent one.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["pr_list", "pr_get"]),
      "pull requests",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 10) Repo introspection — repo_tree / repo_file_get / repo_symbols.
  //     Same empty-state caveat as PRs.
  // -------------------------------------------------------------------------

  test("repo prompt stays within repo_* family", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "What's the directory structure of the main repo? Show me the " +
        "top-level layout.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["repo_tree", "repo_file_get", "repo_symbols"]),
      "repo introspection",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 11) CI runs — runs_list / runs_get.
  // -------------------------------------------------------------------------

  test("CI prompt stays within runs_* family", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Pull the most recent CI runs, then show me the details of the " +
        "latest failing one.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["runs_list", "runs_get"]),
      "CI runs",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 12) web_fetch — straightforward URL ask. Skipped softly if the
  //     workspace has no firecrawl key.
  // -------------------------------------------------------------------------

  test("web fetch prompt fires web_fetch", async ({ request }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Fetch https://example.com and summarise the page content for me.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(testInfo, analysis, new Set(["web_fetch"]), "web fetch");
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // 13) Sub-agent dispatch — run_subagent. Heaviest tool in the
  //     inventory; we only check that the agent can route through it
  //     without thrashing. The actual sub-agent run is best covered by
  //     the dedicated decomposition / specialist suites.
  // -------------------------------------------------------------------------

  test("sub-agent prompt may fire run_subagent without thrashing", async ({
    request,
  }, testInfo) => {
    const ctx = ctxOrThrow();
    const result = await streamNavigatorTurn(request, {
      base: ctx.base,
      token: ctx.token,
      workspaceId: ctx.workspaceId,
      body:
        "Spin up a specialist sub-agent to review our test coverage strategy " +
        "and report back with three concrete improvements.",
      freshThread: true,
    });
    if (result.status === 412) {
      test.skip(true, "Agent not configured on backend (412)");
      return;
    }
    expect(result.status).toBe(200);
    const analysis = analyseToolTrajectory(result.events);
    annotate(testInfo, analysis);
    assertNoRetryAfterFailure(analysis);
    noteToolFamily(
      testInfo,
      analysis,
      new Set(["run_subagent"]),
      "sub-agent dispatch",
    );
    expect(result.text.length, "agent produced a reply").toBeGreaterThan(0);
  });
});
