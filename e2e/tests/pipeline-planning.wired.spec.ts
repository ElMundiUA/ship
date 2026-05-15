/**
 * A1 — Planning bundle e2e.
 *
 * Drives one full pass of the ``planning`` routine on a freshly
 * seeded ticket via real shipctl + the configured agent CLI
 * (Cursor by default). The test is the contract:
 *
 *   1. A ticket in ``stage:planning`` carrying a PO Brief in its
 *      body
 *   2. ``shipctl run --routine planning --ticket <ref>`` returns 0
 *   3. After the run, the ticket body carries the
 *      BA Requirements / Technical Architecture / QA Architecture
 *      sections the planner bundle is supposed to write
 *   4. The ticket has transitioned to ``stage:dev_implementation``
 *
 * Cost: one bundle run ≈ 15-20 min on the long tail. Gated on
 * ``E2E_RUN_PIPELINE=1`` so it doesn't fire in laptop dev loops.
 *
 * Required env (set after running
 * ``tools/scripts/seed_e2e_pipeline_workspace.py``):
 *
 *   E2E_SHIP_API_BASE              — Ship backend origin
 *   E2E_PIPELINE_WORKSPACE_ID      — workspace UUID
 *   E2E_PIPELINE_PO_TOKEN          — PO PAT (project + ticket writes)
 *   E2E_PIPELINE_DEV_TOKEN         — dev PAT (shipctl auth)
 *   E2E_PIPELINE_REPO              — sandbox repo full-name
 *   E2E_PIPELINE_GH_TOKEN          — PAT scoped to the sandbox repo
 *   CURSOR_API_KEY (or ANTHROPIC_API_KEY / OPENAI_API_KEY)
 *
 * The test runs against the ``e2e-pipeline`` workspace seeded by
 * ``seed_e2e_pipeline_workspace.py`` — never against prod
 * workspaces. Tracker is memory-mode; the agent's writes land in
 * ``memory_tracker_tickets`` and we read them back through
 * ``/local-tracker/tickets``.
 */

import { expect, test } from "@playwright/test";

import { dispatchAgent } from "../lib/agent-pipeline";
import { dumpArtifact } from "../lib/eval-artifact";
import {
  extractStage,
  hasPipelineCredentials,
  pipelineActivateProject,
  pipelineCreateProject,
  pipelineCreateTicket,
  pipelineGetTicket,
  pipelineSuiteEnv,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


test.describe("[wired] pipeline · planning bundle (A1)", () => {
  test.beforeAll(() => {
    if (!PIPELINE_ENABLED) {
      test.skip(true, "set E2E_RUN_PIPELINE=1 to run pipeline tests");
    }
    if (!hasPipelineCredentials()) {
      test.skip(
        true,
        "pipeline credentials missing — run seed_e2e_pipeline_workspace.py first",
      );
    }
  });

  // One bundle run is long; let Playwright budget for the worst case.
  test.setTimeout(30 * 60 * 1000);

  test("planning bundle writes BA/TechArch/QAArch and transitions to dev_implementation", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    // ---- arrange: project + planning ticket --------------------------------

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-planning-${Date.now().toString(36)}`,
      body:
        "## Why\n\n" +
        "E2E sandbox project for the planning bundle smoke. " +
        "Replace before adding real work.\n",
      description: "Pipeline e2e — planning bundle",
    });
    // ELS-80 gate: new projects land in ``planning`` (Drafts) and the
    // picker drops their tickets. Bump to ``active`` so the dispatcher
    // can claim our seeded ticket without a human in the loop.
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Add /feedback delete endpoint",
      body:
        "## PO Brief\n\n" +
        "Operators need to remove stale feedback rows without " +
        "rebuilding the server. Add `DELETE /feedback/:id` to the " +
        "feedback API; 204 on success, 404 when the id is unknown.\n\n" +
        "## Out of scope\n\n" +
        "- bulk-delete\n" +
        "- soft-delete / restore\n",
      labels: ["stage:planning"],
    });
    testInfo.annotations.push({
      type: "ticket-ref",
      description: ticket.ticketRef,
    });

    // ---- act: shipctl run --routine planning ------------------------------

    const { worktree, result } = await dispatchAgent({
      routine: "planning",
      ticket: ticket.ticketRef,
      timeoutMs: 28 * 60 * 1000,
      // ``--commit-and-pr`` is what engages the sidecar-finish
      // protocol in run.mjs (ELS-120). Without it, the runner
      // skips reading ``.ship/agent-finish.json`` and the agent's
      // outcome never reaches /agent-runs/finish — the ticket
      // would stay on ``stage:planning`` forever. Planning bundles
      // never produce commits, but the flag still needs to be on.
      commitAndPr: true,
    });

    testInfo.annotations.push({
      type: "shipctl-duration-ms",
      description: String(result.durationMs),
    });
    testInfo.annotations.push({
      type: "shipctl-exit",
      description: String(result.code),
    });

    try {
      expect(
        result.code,
        `shipctl exited ${result.code}\nstdout:\n${result.stdout.slice(-2000)}\nstderr:\n${result.stderr.slice(-2000)}`,
      ).toBe(0);

      // ---- assert: finish handler applied planner's writes ----------------
      // Planning bundle's ``description`` overwrites the ticket body
      // (Linear adapter sets the GraphQL description field; memory
      // adapter overwrites the row's ``body`` column). The structured
      // sections — Problem / AC / Architecture / Test plan — should
      // all be present in the new body. ``comment`` is reserved for
      // auditable narration ("what I did and why"), tagged
      // ``[Ship SDLC:role-planning]``.

      const after = await pipelineGetTicket(request, auth, ticket.ticketRef);

      const stage = extractStage(after);
      expect(
        stage,
        `expected stage:dev_implementation after planning, got ${stage}`,
      ).toBe("dev_implementation");

      // Body must carry the planner's spec — not just the original
      // PO Brief.
      expect(
        after.body.length,
        "planner should have rewritten the body (much larger than PO Brief)",
      ).toBeGreaterThan(1500);
      expect(
        after.body,
        "planner body should include acceptance criteria",
      ).toMatch(/##\s*(Acceptance criteria|AC|Acceptance)/i);
      expect(
        after.body,
        "planner body should include architecture plan",
      ).toMatch(/##\s*(Architecture|Technical|Tech)/i);
      expect(
        after.body,
        "planner body should include test plan",
      ).toMatch(/##\s*(Test|QA|Tests)/i);

      // Planner's audit comment carries the human-readable summary.
      const planningComments = after.comments.filter((c) =>
        c.body.includes("[Ship SDLC:role-planning]"),
      );
      expect(
        planningComments.length,
        "planner should leave one [Ship SDLC:role-planning] comment",
      ).toBeGreaterThanOrEqual(1);

      // Drop the artifact for the eval-judge runner.
      const artifactPath = dumpArtifact("planning", {
        meta: {
          ticket_ref: ticket.ticketRef,
          project_id: project.id,
          duration_ms: result.durationMs,
          agent_provider: "cursor",
        },
        inputs: {
          po_brief:
            "## PO Brief\n\n" +
            "Operators need to remove stale feedback rows without " +
            "rebuilding the server. Add `DELETE /feedback/:id` to the " +
            "feedback API; 204 on success, 404 when the id is unknown.\n",
        },
        outputs: {
          body: after.body,
          stage_after: extractStage(after),
          comments: after.comments.map((c) => c.body),
        },
      });
      testInfo.annotations.push({ type: "eval-artifact", description: artifactPath });
    } finally {
      await worktree.cleanup();
    }
  });
});
