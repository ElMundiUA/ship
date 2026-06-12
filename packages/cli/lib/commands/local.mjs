/**
 * `shipctl local` — trigger-(a) local synchronous executor (ELS-248).
 *
 * A scratch session: the operator types an ask, Ship spawns the
 * workspace's agent runtime on a THROWAWAY git worktree, streams the
 * session, prints the scratch diff and stops. By design this command
 * lives entirely outside the (b) control plane:
 *
 *   - NO ticket   — the prompt renders in `mode: "local"` (ELS-246);
 *   - NO lease    — the dispatcher is never called; a crashed local
 *                   session costs nothing (the project_lock-leak
 *                   postmortem class cannot happen here);
 *   - NO push     — the scratch worktree never touches origin; the
 *                   exit path prints the diff and a cleanup hint.
 *
 * Gates (ELS-247): the per-workspace `local_executor.enabled` config
 * scope must be true (FOUNDER DECISION: default-OFF everywhere), and
 * the server-side escalation classifier marks big-feature asks with
 * an ESCALATE *suggestion* — never a block.
 *
 * a→b escalation (ELS-249): on operator confirmation (interactive
 * yes or explicit `--escalate`), the command files a tracker ticket
 * through `POST /v1/.../tracker/tickets` — the same create_ticket
 * adapter surface the reviewer routines use. The new ticket flows
 * through the normal tracker_poller → dispatcher path and leases
 * fresh; the scratch tree itself is never pushed.
 *
 * Env contract (same trio as `shipctl run`):
 *   - SHIP_API_BASE / SHIP_API_TOKEN / SHIP_WORKSPACE_ID
 *   - provider key for the workspace's agent runtime
 *     (CURSOR_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

import { DEFAULT_PROVIDER, runAgent } from "../agents/index.mjs";
import { fetchWithRetry } from "../retry.mjs";
import {
  fetchPoliciesPreamble,
  fetchWorkspaceAgentProvider,
  renderPrompt,
  resolveAgentRole,
} from "./run.mjs";

const SYSTEM_ROLE_SLUG = "system";

const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_DISABLED = 3;
const EXIT_AGENT_FAIL = 7;


// ---------------------------------------------------------------------------
// Args
// ---------------------------------------------------------------------------


function parseArgs(rest) {
  const args = {
    ask: "",
    role: "developer",
    provider: "",
    dryRun: false,
    escalate: false,
    json: false,
  };
  const positional = [];
  for (let i = 0; i < rest.length; i += 1) {
    const a = rest[i];
    if (a === "--role") args.role = rest[++i] || args.role;
    else if (a === "--provider") args.provider = rest[++i] || "";
    else if (a === "--dry-run") args.dryRun = true;
    else if (a === "--escalate") args.escalate = true;
    else if (a === "--json") args.json = true;
    else if (a === "--help" || a === "-h") return { ...args, help: true };
    else positional.push(a);
  }
  args.ask = positional.join(" ").trim();
  return args;
}


function printUsage() {
  console.log(`Usage: shipctl local "<ask>" [options]

Run a local scratch agent session: throwaway worktree, no ticket,
no lease, stop before push. Requires the workspace config scope
local_executor.enabled=true (default OFF).

Options:
  --role <slug>      agent role to render (default: developer)
  --provider <name>  override the workspace agent runtime
  --dry-run          print the rendered prompt and exit
  --escalate         file an escalation ticket without the interactive prompt
  --json             machine-readable result line on stdout
`);
}


// ---------------------------------------------------------------------------
// Ship API helpers (read-only + create-ticket; nothing else)
// ---------------------------------------------------------------------------


async function fetchJson(url, { apiToken, method = "GET", body = null, description }) {
  const res = await fetchWithRetry(
    () =>
      fetch(url, {
        method,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${apiToken}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      }),
    { description },
  );
  return res;
}


async function fetchLocalExecutorEnabled({ apiBase, apiToken, workspaceId }) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/config/local_executor.enabled`;
  try {
    const res = await fetchJson(url, { apiToken, description: "local_executor.enabled" });
    if (!res.ok) return { enabled: false, reason: `config read ${res.status}` };
    const detail = await res.json();
    return { enabled: detail.current_value === true, reason: null };
  } catch (err) {
    return {
      enabled: false,
      reason: err instanceof Error ? err.message : String(err),
    };
  }
}


async function classifyEscalation({ apiBase, apiToken, workspaceId, ask }) {
  // Best-effort: any failure means "no suggestion" (the classifier
  // is a suggestion engine; its absence must not break the run).
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/local-executor/classify`;
  try {
    const res = await fetchJson(url, {
      apiToken,
      method: "POST",
      body: { ask },
      description: "local-executor classify",
    });
    if (!res.ok) return null;
    const verdict = await res.json();
    return verdict?.verdict === "ESCALATE" ? verdict : null;
  } catch {
    return null;
  }
}


async function createEscalationTicket({ apiBase, apiToken, workspaceId, title, body }) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker/tickets`;
  const res = await fetchJson(url, {
    apiToken,
    method: "POST",
    body: { title, body },
    description: "escalation ticket create",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`ticket create failed: ${res.status} ${text.slice(0, 300)}`);
  }
  return res.json();
}


// ---------------------------------------------------------------------------
// Scratch worktree
// ---------------------------------------------------------------------------


function git(argv, opts = {}) {
  const res = spawnSync("git", argv, {
    encoding: "utf8",
    ...(opts.cwd ? { cwd: opts.cwd } : {}),
  });
  if (res.status !== 0 && !opts.allowFail) {
    throw new Error(
      `git ${argv.join(" ")} failed: ${(res.stderr || res.stdout || "").trim()}`,
    );
  }
  return (res.stdout || "").trim();
}


function createScratchWorktree() {
  // The operator's checkout is sacred: we never mutate it. The agent
  // gets a detached worktree at the current HEAD under the system
  // temp dir; deleting it later is one `git worktree remove`.
  const repoRoot = git(["rev-parse", "--show-toplevel"]);
  const baseSha = git(["rev-parse", "HEAD"]);
  const scratchDir = fs.mkdtempSync(path.join(os.tmpdir(), "ship-local-"));
  git(["worktree", "add", "--detach", scratchDir, "HEAD"], { cwd: repoRoot });
  return { repoRoot, scratchDir, baseSha };
}


function scratchDiffSummary(scratchDir, baseSha) {
  // Diff against the worktree's creation point — covers both local
  // commits the agent made (detached HEAD moved forward) and
  // uncommitted edits. No upstream exists on a detached worktree.
  const stat = git(["diff", baseSha, "--stat"], { cwd: scratchDir, allowFail: true });
  const untracked = git(
    ["ls-files", "--others", "--exclude-standard"],
    { cwd: scratchDir, allowFail: true },
  );
  const commits = git(
    ["log", "--oneline", `${baseSha}..HEAD`],
    { cwd: scratchDir, allowFail: true },
  );
  return { stat, untracked, commits };
}


// ---------------------------------------------------------------------------
// Interactive confirm (escalation is operator-confirmed, never forced)
// ---------------------------------------------------------------------------


async function confirmEscalation() {
  if (!process.stdin.isTTY) return false;
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  const answer = await new Promise((resolve) => {
    rl.question("File an escalation ticket for this ask? [y/N] ", resolve);
  });
  rl.close();
  return /^y(es)?$/i.test((answer || "").trim());
}


// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------


export async function localCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printUsage();
    process.exit(EXIT_OK);
  }
  if (!args.ask) {
    printUsage();
    console.error("error: an ask is required, e.g. shipctl local \"tighten the hero copy\"");
    process.exit(EXIT_USAGE);
  }

  const apiBase = (process.env.SHIP_API_BASE || "").replace(/\/+$/, "");
  const apiToken = process.env.SHIP_API_TOKEN || "";
  const workspaceId = process.env.SHIP_WORKSPACE_ID || "";
  if (!apiBase || !apiToken || !workspaceId) {
    console.error(
      "shipctl local requires SHIP_API_BASE + SHIP_API_TOKEN + SHIP_WORKSPACE_ID",
    );
    process.exit(EXIT_USAGE);
  }

  // Gate 1 (ELS-247): per-workspace opt-in flag, default OFF.
  const gate = await fetchLocalExecutorEnabled({ apiBase, apiToken, workspaceId });
  if (!gate.enabled) {
    console.error(
      "local executor is disabled for this workspace" +
        (gate.reason ? ` (${gate.reason})` : "") +
        ".\nEnable it via Settings → Config → local_executor.enabled " +
        "(or PUT /v1/workspaces/{ws}/config/local_executor.enabled).",
    );
    process.exit(EXIT_DISABLED);
  }

  // Role + system + policies — the same thesis-5 injection the (b)
  // path gets; only the exit protocol differs (ELS-246).
  const [roleResolved, systemResolved] = await Promise.all([
    resolveAgentRole({ apiBase, apiToken, workspaceId, slug: args.role }),
    resolveAgentRole({
      apiBase,
      apiToken,
      workspaceId,
      slug: SYSTEM_ROLE_SLUG,
      optional: true,
    }),
  ]);
  if (!roleResolved) {
    console.error(`unknown agent role '${args.role}' for this workspace`);
    process.exit(EXIT_USAGE);
  }
  const policiesPreamble = await fetchPoliciesPreamble({
    apiBase,
    apiToken,
    workspaceId,
    role: args.role,
  });

  const rendered = renderPrompt({
    patternBody: roleResolved.prompt || "",
    baseBody: systemResolved?.prompt || "",
    role: args.role,
    routineSpec: {},
    task: null,
    fsmStage: null,
    finishCtx: null,
    mode: "local",
    localAsk: args.ask,
  });
  const prompt = policiesPreamble
    ? `${policiesPreamble.trim()}\n\n---\n\n${rendered}`
    : rendered;

  if (args.dryRun || ctx.dryRun) {
    console.log(prompt);
    process.exit(EXIT_OK);
  }

  // Gate 2 (ELS-247): escalation classifier — a SUGGESTION, rendered
  // before the run so the operator can bail early; the run proceeds
  // regardless (suggest-not-block).
  const suggestion = await classifyEscalation({ apiBase, apiToken, workspaceId, ask: args.ask });
  if (suggestion) {
    console.error(
      `\n[ship] escalation suggestion: ${suggestion.reason || "this looks like tracked-feature work"}\n` +
        "[ship] the local run proceeds; you can escalate to a ticket at the end.\n",
    );
  }

  const { scratchDir, baseSha } = createScratchWorktree();
  console.error(`[ship] scratch worktree: ${scratchDir}`);

  const provider =
    args.provider ||
    (await fetchWorkspaceAgentProvider({ apiBase, apiToken, workspaceId })) ||
    DEFAULT_PROVIDER;

  let runtime;
  try {
    runtime = await runAgent(provider, {
      workdir: scratchDir,
      branchName: "local-scratch",
      prompt,
    });
  } catch (err) {
    console.error(
      `[ship] agent launch failed: ${err instanceof Error ? err.message : err}`,
    );
    console.error(`[ship] scratch worktree kept at ${scratchDir}`);
    process.exit(EXIT_AGENT_FAIL);
  }

  const diff = scratchDiffSummary(scratchDir, baseSha);
  console.error("\n[ship] local scratch session finished.");
  if (diff.commits) {
    console.error(`\nScratch commits:\n${diff.commits}`);
  }
  if (diff.stat) {
    console.error(`\nScratch diff (vs HEAD):\n${diff.stat}`);
  }
  if (diff.untracked) {
    console.error(`\nNew files:\n${diff.untracked}`);
  }
  if (!diff.stat && !diff.untracked && !diff.commits) {
    console.error("\nNo changes in the scratch tree.");
  }
  console.error(
    `\nReview at: ${scratchDir}\nClean up:  git worktree remove --force ${scratchDir}`,
  );

  // a→b escalation (ELS-249): operator-confirmed, create_issue only.
  let escalatedTicket = null;
  if (suggestion && (args.escalate || (await confirmEscalation()))) {
    const title =
      suggestion.suggested_title ||
      args.ask.split("\n", 1)[0].slice(0, 120);
    const body = [
      "## Escalated from a local scratch session",
      "",
      "**Operator ask:**",
      "",
      args.ask,
      "",
      "**Scratch outcome (not pushed — re-derive under the (b) lease):**",
      "",
      "```",
      diff.stat || "(no diff)",
      "```",
      diff.untracked ? `New files:\n\`\`\`\n${diff.untracked}\n\`\`\`` : "",
      "",
      `_Filed by \`shipctl local\`; classifier: ${suggestion.reason || "escalation suggested"}_`,
    ]
      .filter((line) => line !== "")
      .join("\n");
    try {
      escalatedTicket = await createEscalationTicket({
        apiBase,
        apiToken,
        workspaceId,
        title,
        body,
      });
      console.error(
        `\n[ship] escalation ticket created: ${escalatedTicket.ticket_ref}` +
          (escalatedTicket.url ? ` (${escalatedTicket.url})` : ""),
      );
    } catch (err) {
      console.error(
        `[ship] escalation failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  if (args.json) {
    console.log(
      JSON.stringify({
        status: runtime.status === "FINISHED" ? "ok" : "agent_error",
        provider,
        scratch_dir: scratchDir,
        diff_stat: diff.stat || null,
        escalation_suggested: Boolean(suggestion),
        escalated_ticket: escalatedTicket?.ticket_ref || null,
      }),
    );
  }
  process.exit(runtime.status === "FINISHED" ? EXIT_OK : EXIT_AGENT_FAIL);
}
