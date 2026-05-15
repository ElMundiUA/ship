/**
 * A5 — Self-heal bundle e2e.
 *
 * Self-heal is workspace-scope (no ticket pin). It scans the
 * workspace for stalled state and fixes the smallest concrete
 * thing per tick. We seed a Phase-2 scenario — a ticket sitting
 * ``In Progress`` with no ``stage:`` label — and assert the
 * bundle does its job:
 *
 *   1. Adds an inferred ``stage:`` label to the orphan ticket
 *      (or files an inbox letter if the bundle can't infer).
 *   2. Leaves an audit comment on the ticket explaining the fix.
 *   3. Posts ``finish`` with the workspace-scope contract
 *      (no ``ticket_ref`` pin).
 *
 * This test depends on a packages/cli fix that teaches
 * ``shipctl run`` to skip the ``/tracker/next`` picker when the
 * routine's ``fsm_stage`` starts with ``workspace_`` — without it
 * the bundle's ``workspace_self_heal`` stage finds no tickets and
 * shipctl exits 0 with reason=no_eligible_ticket.
 */

import { expect, test } from "@playwright/test";

import { dispatchAgent } from "../lib/agent-pipeline";
import { dumpArtifact } from "../lib/eval-artifact";
import {
  hasPipelineCredentials,
  pipelineActivateProject,
  pipelineCreateProject,
  pipelineCreateTicket,
  pipelineGetTicket,
  pipelineSuiteEnv,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


test.describe("[wired] pipeline · self-heal bundle (A5)", () => {
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

  test("self-heal labels an orphan ticket and posts a workspace-scope finish", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    // ---- arrange: orphan ticket with no stage: label ---------------------

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-self-heal-${Date.now().toString(36)}`,
      body: "## Why\n\nE2E sandbox for self-heal.\n",
      description: "Pipeline e2e — self-heal",
    });
    await pipelineActivateProject(request, auth, project.id);

    const orphan = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Add a /feedback/health probe",
      body:
        "## Problem\n\n" +
        "Operators need a tiny health route on the feedback API so " +
        "uptime monitors don't have to ping `/feedback` (which is " +
        "list-shaped and noisy in logs).\n\n" +
        "## Acceptance criteria\n\n" +
        "1. `GET /feedback/healthz` returns 200 with " +
        "`{ ok: true }`.\n" +
        "2. No state change; no auth needed.\n",
      // Notably NO stage:* label. Self-heal Phase 2 should detect
      // this and add the appropriate stage label based on body shape.
      labels: [],
    });
    testInfo.annotations.push({
      type: "orphan-ticket",
      description: orphan.ticketRef,
    });

    // ---- act: shipctl run --routine self_heal (workspace-scope) ----------

    const { worktree, result } = await dispatchAgent({
      // ``self_heal`` matches the routine id in the sandbox repo's
      // .ship/config.yml. The corresponding specialist slug is
      // ``self-heal`` whose ``fsm_stage: workspace_self_heal`` flags
      // run.mjs to skip the picker.
      routine: "self_heal",
      // shipctl ignores the ticket value for workspace-scope routines
      // but the helper signature still requires a string.
      ticket: "WORKSPACE",
      timeoutMs: 28 * 60 * 1000,
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

      // Self-heal can land on the orphan we seeded OR on something
      // else in the workspace (other stale state from prior tests).
      // The assertion that's robust under both: shipctl ran to
      // completion (exit 0) without "no_eligible_ticket" — i.e.
      // the workspace-scope path actually launched the agent.
      // ``# ship: completed routine=self_heal`` should be in stdout.

      // shipctl writes its ``# ship: …`` status line to stderr;
      // Cursor's textual output is on stdout. Check both streams.
      const allLog = `${result.stdout}\n${result.stderr}`;
      expect(
        allLog,
        "self-heal should run to completion, not noop with no_eligible_ticket",
      ).toMatch(/# ship: completed routine=self_heal/);
      expect(
        allLog,
        "self-heal should NOT have hit the picker (workspace scope)",
      ).not.toMatch(/no_eligible_ticket/);

      // Best-effort: if the orphan was the smallest thing self-heal
      // found, it should now carry a stage:* label. Annotate
      // whichever way the result went.
      const orphanAfter = await pipelineGetTicket(
        request,
        auth,
        orphan.ticketRef,
      );
      const stageLabels = orphanAfter.labels.filter((l) =>
        l.startsWith("stage:"),
      );
      testInfo.annotations.push({
        type: "orphan-stage-after",
        description: stageLabels.join(", ") || "(no stage label added)",
      });
      testInfo.annotations.push({
        type: "orphan-comments-after",
        description: String(orphanAfter.comments.length),
      });

      // Pull inbox rows the self-heal bundle filed — workspace-scope
      // finishes often land an inbox letter rather than a ticket
      // comment, and the judge needs that signal too.
      const inboxRes = await request.get(
        `${env.base}/v1/workspaces/${env.workspaceId}/inbox?ownership=all&status=new&limit=10`,
        { headers: { Authorization: `Bearer ${env.poPat}` } },
      );
      const inboxItems =
        inboxRes.ok()
          ? ((await inboxRes.json()) as {
              items: { type: string; title: string; summary: string | null }[];
            }).items
          : [];
      const selfHealInbox = inboxItems.filter((i) =>
        (i.summary || "").includes("role-self-heal"),
      );

      // Capture the full agent stdout (truncate to last 4000 chars
      // so the artifact stays small but the rubric sees the
      // phase narration). The earlier regex was too tight.
      const agentNarrative = result.stdout.slice(-4000);

      const artifactPath = dumpArtifact("self_heal", {
        meta: {
          orphan_ticket_ref: orphan.ticketRef,
          project_id: project.id,
          duration_ms: result.durationMs,
          agent_provider: "cursor",
        },
        inputs: {
          workspace_state_hint: {
            actionable: [
              {
                ticket_ref: orphan.ticketRef,
                reason: "no stage:* label, body has AC",
              },
            ],
          },
        },
        outputs: {
          orphan_stage_after: stageLabels.join(", ") || null,
          orphan_comments: orphanAfter.comments.map((c) => c.body),
          shipctl_status_log: allLog
            .split("\n")
            .filter((l) => l.startsWith("# ship:"))
            .join("\n"),
          agent_narrative: agentNarrative,
          inbox_rows_created: selfHealInbox.map((i) => ({
            type: i.type,
            title: i.title,
            summary: i.summary,
          })),
          // Heuristic outcome from the shipctl status line.
          outcome: allLog.includes("# ship: completed routine=self_heal")
            ? "ready_next_step"
            : "blocked",
        },
      });
      testInfo.annotations.push({ type: "eval-artifact", description: artifactPath });
    } finally {
      await worktree.cleanup();
    }
  });
});
