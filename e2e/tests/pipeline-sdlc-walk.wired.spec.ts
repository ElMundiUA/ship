/**
 * B — Full SDLC walk.
 *
 * One end-to-end pass through every bundle on a single project's
 * lineage:
 *
 *   A0  Navigator chat  → ``project_create`` lands project + anchor
 *   ↓
 *   A2  decomposition   → anchor's project gets WBS/Arch/Test arch
 *                         + N child tickets, project priority parked
 *   ↓
 *   A1  planning        → pick first child, drive planning bundle,
 *                         body grows to spec, transition to dev
 *   ↓
 *   A3  dev_implementation → Cursor writes code, opens PR, transition
 *                            to validation
 *   ↓
 *   A4  validation      → bundle reviews the dev PR, leaves comment
 *
 * Cost: ~6-8 minutes per run. Gated on ``E2E_RUN_PIPELINE=1`` and
 * skipped in CI by default — too long for every commit.
 *
 * The walk asserts each stage's transition AND the *output* of the
 * stage (PR open on GitHub, body sections written, etc.), so a
 * regression in any single bundle surfaces at the offending step
 * rather than only as a downstream cascade.
 */

import { spawnSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import { dispatchAgent } from "../lib/agent-pipeline";
import {
  extractStage,
  hasPipelineCredentials,
  pipelineCreateProject,
  pipelineCreateTicket,
  pipelineGetProject,
  pipelineGetProjectPriority,
  pipelineGetTicket,
  pipelineListProjectTickets,
  pipelineSetProjectState,
  pipelineSuiteEnv,
  pipelineTransitionTicket,
} from "../lib/pipeline-helpers";


const PIPELINE_ENABLED = process.env.E2E_RUN_PIPELINE === "1";


test.describe("[wired] pipeline · full SDLC walk (B)", () => {
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

  // 30-minute budget: each agent step costs 1-3 min; the chain is
  // sequential. Tightening would risk wiping out the walk on a
  // worst-case Cursor latency spike.
  test.setTimeout(30 * 60 * 1000);

  test("Navigator → decomposition → planning → dev → validation chains green", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    const cleanupOps: Array<() => Promise<void>> = [];
    const trackCleanup = (fn: () => Promise<void>) => cleanupOps.push(fn);

    try {
      // -----------------------------------------------------------
      // STEP 1 — Seed project + anchor
      // -----------------------------------------------------------
      // A0 covers the Navigator chat → project_create path
      // separately. B is a determinism-sensitive walk; LLM variance
      // in the chat-driven entry blew up earlier runs. Seed via the
      // direct REST surface so the rest of the chain runs against a
      // known starting state.

      const projectName = `e2e-walk-${Date.now().toString(36)}`;
      const project = await pipelineCreateProject(request, auth, {
        name: projectName,
        body:
          "## Why\n\n" +
          "Operators want bulk retention for feedback rows so the " +
          "dashboard stays useful as the dataset ages. Sweep removes " +
          "rows older than N days; a dry-run counter shows what " +
          "would go on the next pass.\n",
        description: "Pipeline e2e — full SDLC walk",
      });
      const projectId = project.id;

      const anchor = await pipelineCreateTicket(request, auth, {
        projectId,
        title: `Anchor: ${projectName}`,
        body:
          `Decomposition anchor for **${projectName}**. ` +
          "Walk-test seeded directly; A0 covers the Navigator-driven " +
          "shape path.\n",
        labels: ["planning:anchor"],
      });
      const anchorRef = anchor.ticketRef;
      testInfo.annotations.push({
        type: "project-id",
        description: projectId,
      });
      testInfo.annotations.push({ type: "anchor", description: anchorRef });

      // -----------------------------------------------------------
      // STEP 2 — A2: tag the anchor for decomposition + run bundle
      // -----------------------------------------------------------
      // Navigator's create_project lands the anchor with
      // ``planning:anchor`` only — no FSM stage. The operator (or
      // the next Navigator turn) is supposed to tag ``stage:decomposition``
      // before the bundle picks it up. We do it here to keep the
      // walk hands-free.

      await pipelineTransitionTicket(
        request,
        auth,
        anchorRef,
        "decomposition",
      );

      const { worktree: decompWorktree, result: decompResult } =
        await dispatchAgent({
          routine: "decomposition",
          ticket: anchorRef,
          timeoutMs: 28 * 60 * 1000,
          commitAndPr: true,
        });
      trackCleanup(() => decompWorktree.cleanup());
      expect(
        decompResult.code,
        `decomposition shipctl exited ${decompResult.code}\nstderr:\n${decompResult.stderr.slice(-2000)}`,
      ).toBe(0);

      const projAfterDecomp = await pipelineGetProject(
        request,
        auth,
        projectId,
      );
      expect(projAfterDecomp.body || "", "decomp must write WBS section")
        .toMatch(/##\s*WBS/i);
      expect(
        await pipelineGetProjectPriority(request, auth, projectId),
        "decomp must flip the project to parked",
      ).toBe("parked");

      const childrenRaw = await pipelineListProjectTickets(
        request,
        auth,
        projectId,
      );
      const children = childrenRaw.filter(
        (t) => t.ticket_ref !== anchorRef,
      );
      expect(
        children.length,
        "decomp must carve at least one child ticket",
      ).toBeGreaterThanOrEqual(1);
      const childRef = children[0]!.ticket_ref;
      testInfo.annotations.push({
        type: "first-child",
        description: childRef,
      });

      // -----------------------------------------------------------
      // STEP 3 — A1: planning on the first child
      // -----------------------------------------------------------
      // Decomp parked the project; planning's picker wants ``active``
      // for the child. Hand-off step the operator would normally do
      // from the Drafts → Active drag on the dashboard.

      await pipelineSetProjectState(request, auth, projectId, "active");
      await pipelineTransitionTicket(request, auth, childRef, "planning");

      const { worktree: planWt, result: planRes } = await dispatchAgent({
        routine: "planning",
        ticket: childRef,
        timeoutMs: 28 * 60 * 1000,
        commitAndPr: true,
      });
      trackCleanup(() => planWt.cleanup());
      expect(
        planRes.code,
        `planning shipctl exited ${planRes.code}\nstderr:\n${planRes.stderr.slice(-2000)}`,
      ).toBe(0);

      const childAfterPlan = await pipelineGetTicket(request, auth, childRef);
      expect(
        extractStage(childAfterPlan),
        "planning must move child to stage:dev_implementation",
      ).toBe("dev_implementation");
      expect(
        childAfterPlan.body.length,
        "planning must rewrite child body to a full spec",
      ).toBeGreaterThan(1000);

      // -----------------------------------------------------------
      // STEP 4 — A3: dev opens the PR
      // -----------------------------------------------------------

      const { env: pipeEnv, worktree: devWt, result: devRes } =
        await dispatchAgent({
          routine: "dev_implementation",
          ticket: childRef,
          timeoutMs: 28 * 60 * 1000,
          commitAndPr: true,
        });
      trackCleanup(() => devWt.cleanup());
      expect(
        devRes.code,
        `dev shipctl exited ${devRes.code}\nstderr:\n${devRes.stderr.slice(-2000)}`,
      ).toBe(0);

      const childAfterDev = await pipelineGetTicket(request, auth, childRef);
      const devComment = childAfterDev.comments.find((c) =>
        c.body.includes("[Ship SDLC:role-developer]"),
      );
      expect(
        devComment,
        "dev must leave a [Ship SDLC:role-developer] comment",
      ).toBeDefined();
      const prUrlMatch = devComment!.body.match(
        /https:\/\/github\.com\/[^\s]+\/pull\/(\d+)/,
      );
      expect(
        prUrlMatch,
        "dev comment must carry the PR URL spliced by run.mjs",
      ).not.toBeNull();
      const prNumber = prUrlMatch![1]!;
      const prBranch = `cursor/ship-dev_implementation-${childRef.toLowerCase()}-`;
      testInfo.annotations.push({
        type: "pr-url",
        description: prUrlMatch![0],
      });

      // Stash the PR number + branch for cleanup. We extract the
      // actual branch name from the gh API since the local randomness
      // suffix isn't predictable from outside the runner.
      const ghJson = spawnSync(
        "gh",
        [
          "pr",
          "view",
          prNumber,
          "--repo",
          pipeEnv.repoFullName,
          "--json",
          "headRefName",
        ],
        { encoding: "utf8" },
      );
      const realBranch = JSON.parse(ghJson.stdout || '{}')
        .headRefName as string;
      expect(
        realBranch,
        "gh pr view must report a Cursor-shaped branch name",
      ).toMatch(/^cursor\/ship-dev_implementation-/i);
      trackCleanup(async () => {
        spawnSync("gh", [
          "pr",
          "close",
          prNumber,
          "--repo",
          pipeEnv.repoFullName,
          "--delete-branch",
          "--comment",
          "e2e walk teardown",
        ]);
      });

      expect(
        extractStage(childAfterDev),
        "dev must transition the child out of dev_implementation",
      ).not.toBe("dev_implementation");

      // -----------------------------------------------------------
      // STEP 5 — A4: validation reviews the open PR
      // -----------------------------------------------------------

      const { worktree: valWt, result: valRes } = await dispatchAgent({
        routine: "validation",
        ticket: childRef,
        timeoutMs: 28 * 60 * 1000,
        commitAndPr: true,
        // Validator works on top of the dev PR's branch so its
        // commits extend that PR rather than opening a fresh one
        // off main.
        baseBranch: realBranch,
      });
      trackCleanup(() => valWt.cleanup());
      expect(
        valRes.code,
        `validation shipctl exited ${valRes.code}\nstderr:\n${valRes.stderr.slice(-2000)}`,
      ).toBe(0);

      const childAfterVal = await pipelineGetTicket(request, auth, childRef);
      const valStage = extractStage(childAfterVal);
      const valComment = childAfterVal.comments.find((c) =>
        c.body.includes("[Ship SDLC:role-validation]"),
      );

      // Validator can land in three terminal shapes:
      //   - ready_next_step (no defects)        → ticket comment + transition
      //   - blocked (defects found)             → inbox blocker, no transition, no ticket comment
      //   - needs_clarification (spec too thin) → ticket comment + needs:clarification label
      // Accept all three by checking for *any* validation footprint.

      let inboxValBlocker = false;
      if (!valComment && valStage === "validation") {
        const inboxRes = await request.get(
          `${auth.base}/v1/workspaces/${auth.workspaceId}/inbox?ownership=all&status=new&type=blocker&limit=20`,
          { headers: { Authorization: `Bearer ${auth.poPat}` } },
        );
        if (inboxRes.ok()) {
          const json = (await inboxRes.json()) as {
            items: { summary: string | null }[];
          };
          inboxValBlocker = json.items.some((i) =>
            (i.summary || "").includes("[Ship SDLC:role-validation]"),
          );
        }
      }

      expect(
        Boolean(valComment) || inboxValBlocker || valStage !== "validation",
        `validation produced no footprint: comment=${Boolean(valComment)} ` +
          `inbox_blocker=${inboxValBlocker} stage=${valStage}`,
      ).toBe(true);

      testInfo.annotations.push({
        type: "walk-end-stage",
        description: valStage ?? "(no stage)",
      });
      testInfo.annotations.push({
        type: "validation-outcome",
        description: valComment
          ? "ready_next_step (comment + transition)"
          : inboxValBlocker
            ? "blocked (inbox letter)"
            : valStage !== "validation"
              ? "transitioned without role-validation tag"
              : "unknown",
      });
    } finally {
      // Run cleanups in reverse so PR deletion happens before the
      // git-clone temp dirs are nuked (gh CLI doesn't need the
      // local clone, but keeps the order predictable).
      for (const op of cleanupOps.reverse()) {
        try {
          await op();
        } catch {
          /* swallow — best effort */
        }
      }
    }
  });
});
