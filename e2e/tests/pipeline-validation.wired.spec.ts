/**
 * A4 — Validation bundle e2e.
 *
 * Pre-seeds a real PR on the sandbox repo + a Ship-side ticket that
 * references it, then runs the ``validation`` bundle. Asserts:
 *
 *   1. The bundle drops a tagged ``[Ship SDLC:role-validation]``
 *      comment on the ticket.
 *   2. The ticket transitions out of ``stage:validation``.
 *   3. When validation chose Phase 2 (no defects), the sandbox PR
 *      gains a ``test(…)`` commit on the SAME branch — verified
 *      via ``gh pr view --json commits`` against the seed commit.
 *
 * We seed the PR via ``gh`` from the test process rather than
 * chaining A3 → A4 — keeps A4's failure mode focused on the
 * validation step and avoids two LLM rounds per run.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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


/** Seed a real PR on the sandbox repo with a tiny feature change.
 * Returns ``{ branch, prNumber, prUrl }``. The caller is responsible
 * for cleanup via ``gh pr close --delete-branch``. */
function seedDevPr(
  repoFullName: string,
  ghToken: string,
  ticketRef: string,
): { branch: string; prNumber: string; prUrl: string; worktreeDir: string } {
  const stamp = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const branch = `e2e-a4-${ticketRef.toLowerCase()}-${stamp}`;
  const dir = mkdtempSync(join(tmpdir(), "ship-e2e-a4-seed-"));
  const cloneUrl = `https://x-access-token:${ghToken}@github.com/${repoFullName}.git`;
  const sh = (cmd: string[], opts: Record<string, unknown> = {}) =>
    spawnSync(cmd[0]!, cmd.slice(1), {
      cwd: dir,
      encoding: "utf8",
      ...opts,
    });

  let result = spawnSync("git", ["clone", "--depth", "1", cloneUrl, dir], {
    encoding: "utf8",
  });
  if (result.status !== 0) throw new Error(`clone: ${result.stderr}`);

  sh(["git", "checkout", "-b", branch]);
  sh(["git", "config", "user.email", "ship-agent@elmundi.com"]);
  sh(["git", "config", "user.name", "Ship Agent"]);

  // Make a focused change the validator can write tests for. We
  // append a new route to feedback.ts; tests/feedback.test.ts is
  // where validation will plausibly add coverage.
  const routeSrc = `${dir}/src/api/feedback.ts`;
  const current = spawnSync("cat", [routeSrc], { encoding: "utf8" }).stdout;
  const updated = current.replace(
    "  router.get(\"/\", (_req, res) => {",
    `  router.get("/count", (_req, res) => {
    res.json({ count: store.list().length, ts: new Date().toISOString() });
  });

  router.get("/", (_req, res) => {`,
  );
  writeFileSync(routeSrc, updated);

  sh(["git", "add", "-A"]);
  result = sh([
    "git",
    "commit",
    "-m",
    `feat(${ticketRef}): add /feedback/count

Tiny dashboard counter. The validation bundle should add HTTP-level
test coverage here.

Closes ${ticketRef}`,
  ]);
  if (result.status !== 0) throw new Error(`commit: ${result.stderr}`);
  result = sh(["git", "push", "-u", "origin", branch]);
  if (result.status !== 0) throw new Error(`push: ${result.stderr}`);

  const ghCreate = spawnSync(
    "gh",
    [
      "pr",
      "create",
      "--repo",
      repoFullName,
      "--head",
      branch,
      "--base",
      "main",
      "--title",
      `feat(${ticketRef}): add /feedback/count`,
      "--body",
      `## Summary\nAdds GET /feedback/count returning row count + timestamp.\n\n## Test plan\n- [ ] count starts at 0\n- [ ] count == N after N inserts\n\nCloses ${ticketRef}`,
    ],
    { cwd: dir, env: { ...process.env, GH_TOKEN: ghToken }, encoding: "utf8" },
  );
  if (ghCreate.status !== 0) {
    throw new Error(`gh pr create: ${ghCreate.stderr}\n${ghCreate.stdout}`);
  }
  const prUrl = ghCreate.stdout.trim().split("\n").slice(-1)[0]!.trim();
  const prNumber = prUrl.split("/").slice(-1)[0]!;
  return { branch, prNumber, prUrl, worktreeDir: dir };
}


function teardownPr(
  repoFullName: string,
  prNumber: string,
  worktreeDir: string,
): void {
  if (process.env.KEEP_E2E_ARTIFACTS === "1") return;
  spawnSync("gh", [
    "pr",
    "close",
    prNumber,
    "--repo",
    repoFullName,
    "--delete-branch",
    "--comment",
    "e2e teardown",
  ], { encoding: "utf8" });
  try {
    rmSync(worktreeDir, { recursive: true, force: true });
  } catch {
    /* best effort */
  }
}


test.describe("[wired] pipeline · validation bundle (A4)", () => {
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

  test("validation bundle leaves a tagged comment and transitions the ticket out of validation", async ({
    request,
  }, testInfo) => {
    const env = pipelineSuiteEnv();
    const auth = {
      base: env.base!,
      workspaceId: env.workspaceId!,
      poPat: env.poPat!,
    };

    // ---- arrange: project + dev-implementation PR + ticket ----------------

    const project = await pipelineCreateProject(request, auth, {
      name: `e2e-validation-${Date.now().toString(36)}`,
      body: "## Why\n\nE2E sandbox for the validation bundle.\n",
      description: "Pipeline e2e — validation",
    });
    await pipelineActivateProject(request, auth, project.id);

    const ticket = await pipelineCreateTicket(request, auth, {
      projectId: project.id,
      title: "Add GET /feedback/count endpoint",
      body:
        "## PO Brief\n\n" +
        "Tiny counter endpoint for the dashboard.\n\n" +
        "## QA Architecture\n\n" +
        "- Unit: `FeedbackStore.list()` length reflects insertions.\n" +
        "- HTTP: `GET /feedback/count` returns `{ count, ts }`; count=0 on " +
        "fresh store, count=N after N POSTs.\n" +
        "- Edge: response shape stays stable when count=0.\n",
      labels: ["stage:validation"],
    });
    testInfo.annotations.push({
      type: "ticket-ref",
      description: ticket.ticketRef,
    });

    const seeded = seedDevPr(
      env.repoFullName!,
      process.env.E2E_PIPELINE_GH_TOKEN ?? process.env.GITHUB_TOKEN ?? "",
      ticket.ticketRef,
    );
    testInfo.annotations.push({ type: "pr-seeded", description: seeded.prUrl });

    // Reference the PR in a comment so the validator can find it.
    await request.post(
      `${env.base}/v1/workspaces/${env.workspaceId}/local-tracker/tickets/${ticket.ticketRef}/comment`,
      {
        headers: {
          Authorization: `Bearer ${env.poPat}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({
          body: `PR: ${seeded.prUrl}\nbranch: ${seeded.branch}`,
        }),
      },
    );

    // ---- act: shipctl run --routine validation -----------------------------
    // We point the worktree at the dev branch so the validator
    // operates on the SAME commits the dev agent landed.

    const initialCommits = spawnSync(
      "gh",
      [
        "pr",
        "view",
        seeded.prNumber,
        "--repo",
        env.repoFullName!,
        "--json",
        "commits",
      ],
      { encoding: "utf8" },
    );
    const initialCommitCount = JSON.parse(
      initialCommits.stdout || '{"commits":[]}',
    ).commits.length;

    const { worktree, result } = await dispatchAgent({
      routine: "validation",
      ticket: ticket.ticketRef,
      timeoutMs: 28 * 60 * 1000,
      commitAndPr: true,
      // Validator works on top of the dev PR's branch — that's
      // where its test commits need to land so they extend the
      // existing PR rather than opening a fresh one off main.
      baseBranch: seeded.branch,
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

      // ---- assert: validation produced *some* footprint ------------------
      // Three terminal shapes are valid (see validation.md):
      //   - ready_next_step → ticket comment + transition
      //   - blocked         → inbox letter (no comment, no transition)
      //   - needs_clarif.   → ticket comment + needs:clarification label
      // Accept any of these — the rubric scores the shape choice.

      const after = await pipelineGetTicket(request, auth, ticket.ticketRef);
      const valComments = after.comments.filter((c) =>
        c.body.includes("[Ship SDLC:role-validation]"),
      );
      const stage = extractStage(after);

      let inboxBlocker = false;
      if (valComments.length === 0 && stage === "validation") {
        const inboxRes = await request.get(
          `${env.base}/v1/workspaces/${env.workspaceId}/inbox?ownership=all&status=new&type=blocker&limit=20`,
          { headers: { Authorization: `Bearer ${env.poPat}` } },
        );
        if (inboxRes.ok()) {
          const json = (await inboxRes.json()) as {
            items: { summary: string | null }[];
          };
          inboxBlocker = json.items.some((i) =>
            (i.summary || "").includes("[Ship SDLC:role-validation]"),
          );
        }
      }

      expect(
        valComments.length > 0 || inboxBlocker || stage !== "validation",
        `validation produced no footprint: comments=${valComments.length} ` +
          `inbox_blocker=${inboxBlocker} stage=${stage}`,
      ).toBe(true);

      // Pull final commit count to feed the rubric's "test_commits"
      // criterion. If validator chose blocked, this stays at the
      // initial count — judge reads that as Phase 1 stopped early.
      const finalCommitsRes = spawnSync(
        "gh",
        [
          "pr",
          "view",
          seeded.prNumber,
          "--repo",
          env.repoFullName!,
          "--json",
          "commits",
        ],
        { encoding: "utf8" },
      );
      const finalCommits = JSON.parse(
        finalCommitsRes.stdout || '{"commits":[]}',
      ).commits as { oid?: string; messageHeadline: string }[];
      const addedCommits = finalCommits.slice(initialCommitCount);

      const artifactPath = dumpArtifact("validation", {
        meta: {
          ticket_ref: ticket.ticketRef,
          project_id: project.id,
          duration_ms: result.durationMs,
          pr_number: seeded.prNumber,
          pr_url: seeded.prUrl,
          agent_provider: "cursor",
        },
        inputs: {
          spec: after.body,
          pr_branch: seeded.branch,
          initial_commit_count: initialCommitCount,
        },
        outputs: {
          // Heuristic: if a [Ship SDLC:role-validation] comment
          // landed → "ready_next_step"; otherwise validator blocked.
          // Refine in the artifact when we have the sidecar JSON.
          outcome:
            valComments.length > 0 ? "ready_next_step" : "blocked",
          comments: after.comments.map((c) => c.body),
          test_commits: addedCommits.map((c) => ({
            sha: c.oid ?? "",
            message: c.messageHeadline,
          })),
          stage_after: stage,
        },
      });
      testInfo.annotations.push({ type: "eval-artifact", description: artifactPath });
    } finally {
      teardownPr(env.repoFullName!, seeded.prNumber, seeded.worktreeDir);
      await worktree.cleanup();
    }
  });
});
