/**
 * Playwright helper for the Process e2e suite — spawns `shipctl run`
 * as a subprocess so each test exercises the real agent pipeline
 * (Cursor / Claude / Codex CLI) end-to-end.
 *
 * Why subprocess and not GHA workflow_dispatch:
 *   - the dispatcher fires the same `shipctl run` invocation either
 *     way; the GHA wrapper only adds runner provisioning, secrets
 *     plumbing, and a 30-min budget.
 *   - in CI we want the test process to *own* the run lifecycle so
 *     we can collect stdout/stderr, fail fast on hang, and clean up
 *     deterministically. workflow_dispatch hands control to GitHub
 *     and our only signal is polling the runs API.
 *   - sandbox repo still ships the `ship-agent-run.yml` workflow —
 *     it's the customer-facing contract — but the e2e doesn't drive
 *     it.
 *
 * Repo handling: each test gets a private worktree under
 * `/tmp/ship-e2e-pipeline-<random>/`. We clone the sandbox repo
 * fresh, run shipctl against it, then push the branch shipctl
 * created. Teardown deletes the worktree + closes/deletes the
 * branch on the remote.
 */

import { spawnSync, spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export type AgentProvider = "cursor" | "claude" | "codex";

export type PipelineEnv = {
  shipApiBase: string;
  shipApiToken: string;
  workspaceId: string;
  repoFullName: string; // e.g. "ElMundiUA/ship-e2e-pipeline"
  agentProvider: AgentProvider;
  githubToken: string; // PAT scoped to the sandbox repo (write)
  shipctlBin: string; // resolved path to the shipctl binary
};

export function loadPipelineEnv(overrides: Partial<PipelineEnv> = {}): PipelineEnv {
  const base = process.env.E2E_SHIP_API_BASE?.trim().replace(/\/+$/, "");
  const token = process.env.E2E_PIPELINE_DEV_TOKEN?.trim() ||
    process.env.E2E_SHIP_API_TOKEN?.trim();
  const ws = process.env.E2E_PIPELINE_WORKSPACE_ID?.trim();
  const repo = process.env.E2E_PIPELINE_REPO?.trim() || "ElMundiUA/ship-e2e-pipeline";
  const provider = (process.env.E2E_PIPELINE_AGENT?.trim() || "cursor") as AgentProvider;
  const ghToken = process.env.E2E_PIPELINE_GH_TOKEN?.trim() ||
    process.env.GITHUB_TOKEN?.trim();
  const shipctl = process.env.E2E_SHIPCTL_BIN?.trim() || "shipctl";

  if (!base) throw new Error("E2E_SHIP_API_BASE not set");
  if (!token) {
    throw new Error("E2E_PIPELINE_DEV_TOKEN (or E2E_SHIP_API_TOKEN) not set");
  }
  if (!ws) throw new Error("E2E_PIPELINE_WORKSPACE_ID not set");
  if (!ghToken) {
    throw new Error("E2E_PIPELINE_GH_TOKEN (or GITHUB_TOKEN) not set");
  }
  return {
    shipApiBase: base,
    shipApiToken: token,
    workspaceId: ws,
    repoFullName: repo,
    agentProvider: provider,
    githubToken: ghToken,
    shipctlBin: shipctl,
    ...overrides,
  };
}

export type Worktree = {
  dir: string;
  branch: string;
  cleanup: () => Promise<void>;
};

/**
 * Clone the sandbox repo into a fresh temp dir on a unique branch.
 * Branch name is `e2e/<routine>-<ticket>-<timestamp>` so concurrent
 * test runs never collide. Caller is responsible for invoking
 * `cleanup()` in `afterEach` — leaves the worktree + remote branch
 * on disk on test failure (`KEEP_E2E_ARTIFACTS=1`) for postmortem.
 */
export function prepareWorktree(
  env: PipelineEnv,
  opts: { routine: string; ticket: string; baseBranch?: string },
): Worktree {
  const stamp = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const branch = `e2e/${opts.routine}-${opts.ticket.toLowerCase()}-${stamp}`;
  const dir = mkdtempSync(join(tmpdir(), "ship-e2e-pipeline-"));
  const cloneUrl = `https://x-access-token:${env.githubToken}@github.com/${env.repoFullName}.git`;
  // Full clone (no --depth=1) when starting from a non-default base
  // so the validator / self-heal worktrees see *both* main and the
  // in-flight branch. shipctl's commit verifier reaches for ``main``
  // as the base ref — single-branch clones starve that lookup.
  const cloneArgs = opts.baseBranch
    ? ["clone", cloneUrl, dir]
    : ["clone", "--depth", "1", cloneUrl, dir];
  const clone = spawnSync("git", cloneArgs, { encoding: "utf8" });
  if (clone.status !== 0) {
    throw new Error(`git clone failed: ${clone.stderr}`);
  }
  if (opts.baseBranch) {
    const co = spawnSync(
      "git",
      ["-C", dir, "checkout", opts.baseBranch],
      { encoding: "utf8" },
    );
    if (co.status !== 0) {
      throw new Error(
        `git checkout ${opts.baseBranch} failed: ${co.stderr}`,
      );
    }
  }
  // Branch from the resolved base so shipctl has a clean base to
  // commit on top of (HEAD points at the base after the clone above).
  const checkout = spawnSync("git", ["-C", dir, "checkout", "-b", branch], {
    encoding: "utf8",
  });
  if (checkout.status !== 0) {
    throw new Error(`git checkout -b ${branch} failed: ${checkout.stderr}`);
  }
  // Configure committer locally so shipctl's commit picks up the
  // dev-bot identity rather than the operator's global git config.
  spawnSync("git", ["-C", dir, "config", "user.email", "ship-agent@elmundi.com"]);
  spawnSync("git", ["-C", dir, "config", "user.name", "Ship Agent"]);

  const cleanup = async () => {
    if (process.env.KEEP_E2E_ARTIFACTS === "1") {
      console.warn(`[e2e] KEEP_E2E_ARTIFACTS=1; worktree retained at ${dir}`);
      return;
    }
    // Delete remote branch (ignore failure — branch may not have
    // been pushed if shipctl bailed before --commit-and-pr).
    spawnSync(
      "git",
      ["-C", dir, "push", "origin", "--delete", branch],
      { encoding: "utf8" },
    );
    if (existsSync(dir)) {
      rmSync(dir, { recursive: true, force: true });
    }
  };
  return { dir, branch, cleanup };
}

export type RunResult = {
  code: number;
  stdout: string;
  stderr: string;
  durationMs: number;
};

/**
 * Run shipctl synchronously and capture output. Long timeouts
 * (default 25 min) cover the bundle prompts — caller can shorten
 * for sad-path tests that expect to bail early.
 */
export async function runShipctl(
  env: PipelineEnv,
  worktree: Worktree,
  opts: {
    routine: string;
    ticket: string;
    timeoutMs?: number;
    commitAndPr?: boolean;
    extraArgs?: string[];
  },
): Promise<RunResult> {
  const args = [
    "run",
    "--routine",
    opts.routine,
    "--ticket",
    opts.ticket,
    "--trigger",
    "e2e",
  ];
  if (opts.commitAndPr ?? true) args.push("--commit-and-pr");
  if (opts.extraArgs) args.push(...opts.extraArgs);

  const started = Date.now();
  const child: ChildProcess = spawn(env.shipctlBin, args, {
    cwd: worktree.dir,
    env: {
      ...process.env,
      SHIP_API_BASE: env.shipApiBase,
      SHIP_API_TOKEN: env.shipApiToken,
      SHIP_WORKSPACE_ID: env.workspaceId,
      SHIP_REPO: env.repoFullName,
      GH_TOKEN: env.githubToken,
      // Hint shipctl which CLI to spawn. Used by the agent resolver
      // when the workspace's bound provider is unset.
      SHIP_AGENT_PROVIDER: env.agentProvider,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout?.on("data", (chunk) => {
    const text = chunk.toString();
    stdout += text;
    if (process.env.E2E_SHIPCTL_VERBOSE === "1") process.stdout.write(text);
  });
  child.stderr?.on("data", (chunk) => {
    const text = chunk.toString();
    stderr += text;
    if (process.env.E2E_SHIPCTL_VERBOSE === "1") process.stderr.write(text);
  });

  const timeoutMs = opts.timeoutMs ?? 25 * 60 * 1000;
  const code: number = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`shipctl run timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.on("close", (c) => {
      clearTimeout(timer);
      resolve(c ?? -1);
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });

  return {
    code,
    stdout,
    stderr,
    durationMs: Date.now() - started,
  };
}

/**
 * Convenience wrapper for the common case: prepare → run → return
 * the worktree so the test can inspect the branch / push artifacts
 * before cleanup.
 *
 * Tests still need to call `worktree.cleanup()` themselves in their
 * afterEach. Returning the handle (rather than auto-cleaning inside
 * this function) lets failing tests retain the worktree for triage.
 */
export async function dispatchAgent(
  opts: {
    routine: string;
    ticket: string;
    timeoutMs?: number;
    commitAndPr?: boolean;
    baseBranch?: string;
    envOverrides?: Partial<PipelineEnv>;
  },
): Promise<{ env: PipelineEnv; worktree: Worktree; result: RunResult }> {
  const env = loadPipelineEnv(opts.envOverrides);
  const worktree = prepareWorktree(env, opts);
  const result = await runShipctl(env, worktree, opts);
  return { env, worktree, result };
}
