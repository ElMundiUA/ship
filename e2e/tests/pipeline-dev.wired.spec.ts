/**
 * A3 — Dev implementation e2e.
 *
 * Drives the developer routine against a ticket whose body already
 * carries the BA/Architecture/Test plan sections that a real
 * planning run would have produced. Asserts:
 *
 *   1. shipctl drives Cursor to write code, commit it, push the
 *      branch, and open a PR on the sandbox repo.
 *   2. ``.ship/agent-finish.json`` carries the PR title/body; the
 *      runner splices the resulting PR URL back into the audit
 *      comment.
 *   3. Ticket transitions out of ``stage:dev_implementation`` (to
 *      ``stage:validation`` per our bundle-form config).
 *
 * The test does NOT chain through real planning — it seeds the
 * ticket body directly with a planning-shaped spec. That keeps A3
 * focused on the dev step and shaves ~1 minute of LLM time per run.
 */

import { spawnSync } from "node:child_process";

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


const DEV_TICKET_BODY = `## PO Brief

Add a tiny \`GET /feedback/count\` endpoint that returns the current
row count from the in-memory feedback store as \`{ "count": <n> }\`.
Useful for the dashboard counter before we wire bulk-delete.

## BA Requirements

- ROUTE: \`GET /feedback/count\` mounted next to the existing
  \`GET /feedback/\` list.
- Response: 200 with JSON body \`{ "count": <integer>, "ts": "<ISO>" }\`
  where \`ts\` is the server's current UTC ISO 8601 timestamp.
- No auth required for this endpoint.

## Technical Architecture

- Add the route to \`src/api/feedback.ts\` (the Express router for
  the feedback resource).
- Pull the count from \`FeedbackStore.list().length\` — keep the
  store unchanged. Reuse the existing router instance returned by
  \`feedbackRouter(store)\`; no new files needed.
- Order the new route BEFORE the parameterised \`router.get("/:id", …)\`
  so Express doesn't shadow \`/count\` as an id lookup.

## QA Architecture

- Vitest at \`tests/feedback.test.ts\`:
  - "GET /feedback/count returns 0 on a fresh store"
  - "GET /feedback/count returns N after N inserts"
- HTTP-level coverage via supertest against the buildApp factory; if
  supertest isn't already a dev dep, leave a note and skip the HTTP
  test rather than adding a dependency.

## Out of scope

- Pagination on the list endpoint.
- Caching the count.
- Persistence beyond the in-memory store.
`;


test.describe("[wired] pipeline · dev implementation (A3)", () => {
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

  test("dev_implementation opens a PR on the sandbox repo and transitions out of dev_implementation", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    // ---- arrange: project + dev-ready ticket ------------------------------

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-dev-${Date.now().toString(36)}`,
      body: "## Why\n\nE2E sandbox for dev_implementation bundle.\n",
      description: "Pipeline e2e — dev",
    });
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Add GET /feedback/count endpoint",
      body: DEV_TICKET_BODY,
      labels: ["stage:dev_implementation"],
    });
    testInfo.annotations.push({
      type: "ticket-ref",
      description: ticket.ticketRef,
    });

    // ---- act: shipctl run --routine dev_implementation ---------------------

    const { env: pipelineEnv, worktree, result } = await dispatchAgent({
      routine: "dev_implementation",
      ticket: ticket.ticketRef,
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
    testInfo.annotations.push({
      type: "branch",
      description: worktree.branch,
    });

    try {
      expect(
        result.code,
        `shipctl exited ${result.code}\nstdout:\n${result.stdout.slice(-2000)}\nstderr:\n${result.stderr.slice(-2000)}`,
      ).toBe(0);

      // ---- assert: ticket carries a PR URL in the audit comment ------------

      const after = await pipelineGetTicket(request, auth, ticket.ticketRef);
      const devComment = after.comments.find((c) =>
        c.body.includes("[Ship SDLC:role-developer]"),
      );
      expect(
        devComment,
        "developer should leave a [Ship SDLC:role-developer] comment",
      ).toBeDefined();
      const prUrlMatch = devComment!.body.match(
        new RegExp(
          `https://github\\.com/${pipelineEnv.repoFullName.replace("/", "/")}/pull/(\\d+)`,
        ),
      );
      expect(
        prUrlMatch,
        `developer comment should carry the PR URL spliced by run.mjs; comment was:\n${devComment!.body}`,
      ).not.toBeNull();
      const prNumber = prUrlMatch![1];
      testInfo.annotations.push({
        type: "pr-url",
        description: prUrlMatch![0],
      });

      // ---- assert: ticket transitioned out of dev_implementation -----------

      const stage = extractStage(after);
      expect(
        stage,
        `expected ticket to leave stage:dev_implementation; still on ${stage}`,
      ).not.toBe("dev_implementation");

      // ---- assert: the PR really exists on the sandbox repo ----------------
      // Verifies the runner actually called ``gh pr create`` rather
      // than just writing the URL into the comment from a sidecar
      // hallucination.

      const ghView = spawnSync(
        "gh",
        [
          "pr",
          "view",
          prNumber,
          "--repo",
          pipelineEnv.repoFullName,
          "--json",
          "state,headRefName,title,body,additions,deletions,changedFiles,files",
        ],
        { encoding: "utf8" },
      );
      expect(
        ghView.status,
        `gh pr view #${prNumber} failed: ${ghView.stderr}`,
      ).toBe(0);
      const prMeta = JSON.parse(ghView.stdout) as {
        state: string;
        headRefName: string;
        title: string;
        body: string;
        additions: number;
        deletions: number;
        changedFiles: number;
        files: { path: string; additions: number; deletions: number }[];
      };
      expect(prMeta.state, "PR should be OPEN").toBe("OPEN");
      expect(prMeta.headRefName).toMatch(/^cursor\/ship-dev_implementation-/);

      const artifactPath = dumpArtifact("dev", {
        meta: {
          ticket_ref: ticket.ticketRef,
          project_id: project.id,
          duration_ms: result.durationMs,
          pr_number: prNumber,
          pr_url: prUrlMatch![0],
          agent_provider: "cursor",
        },
        inputs: {
          spec: DEV_TICKET_BODY,
        },
        outputs: {
          pr_title: prMeta.title,
          pr_body: prMeta.body,
          pr_diff_summary: {
            additions: prMeta.additions,
            deletions: prMeta.deletions,
            changed_files: prMeta.changedFiles,
            files: prMeta.files.map((f) => f.path),
          },
          comments: after.comments.map((c) => c.body),
          stage_after: stage,
        },
      });
      testInfo.annotations.push({ type: "eval-artifact", description: artifactPath });

      // Best-effort cleanup of the PR + branch on the remote so we
      // don't leave dozens of stale PRs on the sandbox repo. Failure
      // logs but does not fail the test.
      if (process.env.KEEP_E2E_ARTIFACTS !== "1") {
        spawnSync("gh", [
          "pr",
          "close",
          prNumber,
          "--repo",
          pipelineEnv.repoFullName,
          "--delete-branch",
          "--comment",
          "e2e teardown",
        ], { encoding: "utf8" });
      }
    } finally {
      await worktree.cleanup();
    }
  });
});
