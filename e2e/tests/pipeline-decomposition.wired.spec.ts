/**
 * A2 — Decomposition bundle e2e.
 *
 * Drives one full pass of the decomposition bundle against a fresh
 * project anchor. Decomposition collapses the legacy 4-stage chain
 * (brief → WBS → architecture → test_architecture → tasks) into a
 * single agent run that:
 *
 *   1. Reads the PO Brief from the anchor description (never
 *      rewrites it).
 *   2. Emits ``project_sections`` for WBS / Architecture /
 *      Test architecture — server upserts each into the project body.
 *   3. Emits ``child_tickets`` — server creates each under the project.
 *   4. Finishes with ``stage_next=planning_done`` + ``process=decomposition``
 *      so the completion hook flips the project's dashboard row from
 *      Drafts → Parked (ELS-81 — operator promotes Parked → Active
 *      manually when ready to ship).
 *
 * Anchor tickets carry the ``planning:anchor`` label, which exempts
 * them from the ELS-80 priority gate. No explicit project activation
 * needed for the picker to claim the anchor.
 */

import { expect, test } from "@playwright/test";

import { dispatchAgent } from "../lib/agent-pipeline";
import { dumpArtifact } from "../lib/eval-artifact";
import {
  hasPipelineCredentials,
  pipelineCreateProject,
  pipelineCreateTicket,
  pipelineGetProject,
  pipelineGetProjectPriority,
  pipelineGetTicket,
  pipelineListProjectTickets,
  pipelineSuiteEnv,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


test.describe("[wired] pipeline · decomposition bundle (A2)", () => {
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

  test.setTimeout(30 * 60 * 1000);

  test("decomposition bundle writes project sections, creates children, parks the project", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    // ---- arrange: project + anchor ticket ---------------------------------

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-decomp-${Date.now().toString(36)}`,
      body:
        "## Why\n\n" +
        "E2E sandbox project for the decomposition bundle smoke. " +
        "Replace before adding real work.\n",
      description: "Pipeline e2e — decomposition",
    });

    // Anchor brief: enough shape that decomposition has a real
    // problem to chew on. Brief stays on the anchor; WBS /
    // Architecture / Test architecture land on the PROJECT body.
    const anchor = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Build feedback retention controls",
      body:
        "## PO Brief\n\n" +
        "Operators want to clean up old feedback rows in bulk: an " +
        "admin-only retention sweep that removes entries older than " +
        "N days, plus a dashboard counter showing what would be " +
        "purged on the next sweep. Goal is to keep the feedback API " +
        "useful as it ages without manual DELETEs.\n\n" +
        "## Goal\n\n" +
        "1. Bulk retention sweep (server-side, scheduled).\n" +
        "2. Dry-run counter visible from the dashboard.\n" +
        "3. Admin-only audit trail of every sweep.\n",
      // planning:anchor exempts the anchor from the ELS-80 priority
      // gate so the picker can claim it without us activating the
      // project. stage:decomposition tells the picker which routine
      // this anchor belongs to.
      labels: ["stage:decomposition", "planning:anchor"],
    });
    testInfo.annotations.push({
      type: "anchor-ref",
      description: anchor.ticketRef,
    });

    // Pre-flight: project body should NOT yet carry decomposition
    // sections — we want the test to prove the bundle wrote them,
    // not happen-to-be-there from a prior run.
    const before = await pipelineGetProject(request, auth, project.id);
    expect(before.body || "").not.toMatch(/##\s*WBS/i);

    // ---- act: shipctl run --routine decomposition --------------------------

    const { worktree, result } = await dispatchAgent({
      routine: "decomposition",
      ticket: anchor.ticketRef,
      timeoutMs: 28 * 60 * 1000,
      commitAndPr: true, // engages the sidecar-finish protocol
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

      // ---- assert: anchor description untouched ---------------------------
      // Decomposition's "Hard rule" — NEVER rewrite the anchor body.
      // The PO brief stays exactly as the PO wrote it.

      const anchorAfter = await pipelineGetTicket(
        request,
        auth,
        anchor.ticketRef,
      );
      expect(anchorAfter.body, "anchor PO brief must stay verbatim").toMatch(
        /## PO Brief/,
      );
      expect(
        anchorAfter.body.length,
        "anchor body should not balloon with project-side artefacts",
      ).toBeLessThan(2000);

      // ---- assert: project body carries the three sections ----------------
      // ``upsert_project_section`` replace-or-appends ``## <name>`` blocks
      // on the project body. Three sections + the auto-rendered Tasks
      // list = at least four discrete blocks land here.

      const projectAfter = await pipelineGetProject(request, auth, project.id);
      const body = projectAfter.body || "";
      expect(body, "project should carry the WBS section").toMatch(
        /##\s*WBS/i,
      );
      expect(body, "project should carry the Architecture section").toMatch(
        /##\s*Architecture/i,
      );
      expect(body, "project should carry the Test architecture section")
        .toMatch(/##\s*Test architecture/i);
      expect(body, "project should carry the Tasks section (auto-rendered)")
        .toMatch(/##\s*Tasks/i);

      // ---- assert: child tickets created under the project ----------------

      const children = await pipelineListProjectTickets(
        request,
        auth,
        project.id,
      );
      // Strip the anchor itself out of the count.
      const realChildren = children.filter(
        (t) => t.ticket_ref !== anchor.ticketRef,
      );
      expect(
        realChildren.length,
        "decomposition should carve at least 2 child tickets out of the WBS",
      ).toBeGreaterThanOrEqual(2);

      // ---- assert: priorities row flipped to ``parked`` --------------------
      // ELS-81 — stage_next=planning_done on process=decomposition
      // triggers the completion hook which moves the priorities row
      // from ``planning`` (drafts) to ``parked`` (PO promotes →
      // ``active`` manually). The tracker-native project state is
      // separate — that stays as the tracker manages it.

      const priorityBucket = await pipelineGetProjectPriority(
        request,
        auth,
        project.id,
      );
      expect(
        priorityBucket,
        `expected priority bucket=parked after decomposition; got ${priorityBucket}`,
      ).toBe("parked");

      // ---- assert: decomposer's audit comment landed ----------------------

      const decompComments = anchorAfter.comments.filter((c) =>
        c.body.includes("[Ship decomposition:role-decomposition]"),
      );
      expect(
        decompComments.length,
        "decomposer should leave one [Ship decomposition:role-decomposition] comment",
      ).toBeGreaterThanOrEqual(1);

      // Fetch each child's full body so the rubric's "child body
      // discipline" criterion has real text to score. The list
      // endpoint returns ticket_ref/title/state only — bodies need
      // a per-ticket round-trip.
      const childDetails = await Promise.all(
        realChildren.map(async (c) => {
          const detail = await pipelineGetTicket(request, auth, c.ticket_ref);
          return {
            ticket_ref: c.ticket_ref,
            title: c.title,
            state: c.state,
            body: detail.body,
            labels: detail.labels,
          };
        }),
      );

      const artifactPath = dumpArtifact("decomposition", {
        meta: {
          ticket_ref: anchor.ticketRef,
          project_id: project.id,
          duration_ms: result.durationMs,
          agent_provider: "cursor",
          // The ``## Tasks`` section in project_body is server-
          // rendered from the agent's ``child_tickets`` array
          // (handler in agent_runs.py:_finish path). The rubric
          // should NOT score this as agent fabrication.
          tasks_section_source: "server",
        },
        inputs: {
          anchor_brief: anchorAfter.body,
        },
        outputs: {
          project_body: projectAfter.body,
          priority_bucket: priorityBucket,
          child_tickets: childDetails,
          comments: anchorAfter.comments.map((c) => c.body),
        },
      });
      testInfo.annotations.push({ type: "eval-artifact", description: artifactPath });
    } finally {
      await worktree.cleanup();
    }
  });
});
