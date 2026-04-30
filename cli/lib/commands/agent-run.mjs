/**
 * `shipctl agent-run` — E14 routine entry-point.
 *
 * Customer's GH Actions cron fires this once per routine slot. The
 * pipeline:
 *
 *   1. Read the routine from `.ship/config.yml` (pattern_id, optional
 *      user-authored prompt).
 *   2. Fetch the pattern body+frontmatter from Ship server
 *      (`POST /fetch kind=pattern id=<pattern_id>`).
 *   3. If the pattern declares `spec.fsm_stage`, ask Ship server for
 *      the next ticket in that FSM stage
 *      (`GET /v1/.../tracker/next?state=<stage>`). Server picks the
 *      adapter (Linear / GH Issues / etc.) — CLI doesn't care.
 *   4. Render the prompt (pattern body + ticket details + hand-off
 *      instructions for `.ship/run-state.json`).
 *   5. Launch the configured agent runtime (`cli/lib/agents/`) — Cursor
 *      Cloud today. Block until the runtime terminates.
 *   6. Read `.ship/run-state.json` from the agent's branch
 *      (`{ state, comment?, transition_to?, payload? }`).
 *   7. Apply via Ship server endpoints:
 *
 *        - state=ready_next_step    → POST /tracker/transition
 *        - state=human_validation   → POST /tracker/comment + /inbox/items
 *        - state=blocked            → POST /inbox/items
 *
 *   8. Exit 0 / non-0 with a structured summary on stdout (`--json`).
 *
 * Env contract (typically wired in `ship-trigger-schedule.yml`):
 *   - SHIP_API_BASE         — Ship server, e.g. https://ship.elmundi.com
 *   - SHIP_API_TOKEN        — workspace API token (admin scope)
 *   - SHIP_WORKSPACE_ID     — UUID of the workspace this run belongs to
 *   - SHIP_REPO_ID          — UUID of the WorkspaceRepo row
 *   - CURSOR_API_KEY        — Cursor Cloud agent API key (when provider=cursor)
 *   - GITHUB_REPOSITORY     — owner/repo (used to resolve the agent's branch back)
 *   - GITHUB_TOKEN          — read-only repo access (to fetch run-state.json)
 */

import path from "node:path";

import yaml from "yaml";

import { readConfig, findShipRoot } from "../config/io.mjs";
import { resolveExecutable } from "../runtime/routines.mjs";
import { fetchArtifact } from "../http.mjs";
import { resolveProvider, runAgent } from "../agents/index.mjs";


const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_NO_TASK = 0; // intentional: no eligible ticket is a clean noop
const EXIT_BLOCKED = 5;
const EXIT_HUMAN = 6;
const EXIT_AGENT_FAIL = 7;
const EXIT_API_FAIL = 8;


// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------


export async function agentRunCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }
  if (!args.routine) {
    die(EXIT_USAGE, "`--routine <id>` is required.\nRun: shipctl agent-run --help");
  }

  const cwd = args.cwd || process.cwd();
  const root = findShipRoot(cwd);
  if (!root) {
    die(EXIT_USAGE, `.ship/config.yml not found (searched from ${path.resolve(cwd)} upward).`);
  }

  const { config } = readConfig(cwd);
  const resolved = resolveExecutable(config, args.routine);
  if (!resolved) {
    die(EXIT_USAGE, `unknown routine '${args.routine}' in .ship/config.yml`);
  }

  const env = readEnv();
  const { apiBase, apiToken, workspaceId, repoId, githubRepo, githubToken } = env;

  // 1) Resolve pattern
  const patternId = resolved.executable.pattern;
  if (!patternId) {
    die(EXIT_USAGE, `routine '${args.routine}' has no pattern set`);
  }

  const fetchBase = methodologyBase(env, config);
  const { content: rawPatternBody } = await fetchArtifact(fetchBase, "pattern", patternId);
  const { frontmatter, body: patternBody } = splitFrontmatter(rawPatternBody);
  const fsmStage = pickFsmStage(frontmatter);

  // Patterns reference ``{{BASE}}`` to splice in common-base. Fetch it
  // up-front so renderPrompt can do the substitution. ``{{SKILLS_CONTEXT}}``
  // inside common-base is left as "(no skills directory)" for the MVP —
  // skills bundling lands in a follow-up.
  const baseBody = await fetchCommonBase(fetchBase);

  // 2) Resolve task
  let task = null;
  if (fsmStage) {
    task = await getNextTask({
      apiBase,
      apiToken,
      workspaceId,
      repoId,
      state: fsmStage,
    });
    if (!task) {
      emit(args, { status: "noop", routine: args.routine, pattern: patternId, fsm_stage: fsmStage, reason: "no_eligible_ticket" });
      process.exit(EXIT_NO_TASK);
    }
  }

  // 3) Render prompt
  const prompt = renderPrompt({
    patternBody,
    baseBody,
    role: patternId,
    routineSpec: resolved.executable,
    task,
    fsmStage,
  });

  // 4) Launch agent runtime
  const provider = resolveProvider(config, args.routine);
  const branchName = makeBranchName(args.routine, task?.ticket_ref);
  const repoUrl = githubRepo ? `https://github.com/${githubRepo}` : null;
  if (!repoUrl) die(EXIT_USAGE, "GITHUB_REPOSITORY env var is required to launch agent");

  let runtime;
  try {
    runtime = await runAgent(provider, {
      repoUrl,
      ref: env.githubRef || "main",
      branchName,
      prompt,
      autoCreatePr: false,
    });
  } catch (err) {
    emit(args, {
      status: "error",
      routine: args.routine,
      pattern: patternId,
      stage: "launch_agent",
      error: err instanceof Error ? err.message : String(err),
    });
    process.exit(EXIT_AGENT_FAIL);
  }

  // 5) Read .ship/run-state.json from agent's branch
  let stateFile;
  try {
    stateFile = await readBranchFile({
      repo: githubRepo,
      branch: runtime.branchName,
      path: ".ship/run-state.json",
      githubToken,
    });
  } catch (err) {
    emit(args, {
      status: "error",
      routine: args.routine,
      pattern: patternId,
      stage: "read_state",
      branch: runtime.branchName,
      error: err instanceof Error ? err.message : String(err),
    });
    process.exit(EXIT_AGENT_FAIL);
  }

  let agentState;
  try {
    agentState = JSON.parse(stateFile);
  } catch (err) {
    emit(args, {
      status: "error",
      routine: args.routine,
      pattern: patternId,
      stage: "parse_state",
      error: `.ship/run-state.json is not valid JSON: ${err.message}`,
    });
    process.exit(EXIT_AGENT_FAIL);
  }

  // 6) Apply via Ship server
  const apply = await applyAgentState({
    apiBase,
    apiToken,
    workspaceId,
    repoId,
    routine: args.routine,
    pattern: patternId,
    task,
    state: agentState,
    fsmStage,
  });

  emit(args, {
    status: apply.exitCode === EXIT_OK ? "completed" : apply.statusName,
    routine: args.routine,
    pattern: patternId,
    fsm_stage: fsmStage,
    ticket_ref: task?.ticket_ref || null,
    agent_id: runtime.agentId,
    branch: runtime.branchName,
    state: agentState.state,
    actions: apply.actions,
  });
  process.exit(apply.exitCode);
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function readEnv() {
  return {
    apiBase: stripSlash(process.env.SHIP_API_BASE || ""),
    apiToken: process.env.SHIP_API_TOKEN || "",
    workspaceId: process.env.SHIP_WORKSPACE_ID || "",
    repoId: process.env.SHIP_REPO_ID || "",
    githubRepo: process.env.GITHUB_REPOSITORY || "",
    githubRef: (process.env.GITHUB_REF_NAME || "main").trim(),
    githubToken: process.env.GITHUB_TOKEN || "",
  };
}


function stripSlash(s) {
  return s.replace(/\/+$/, "");
}


function methodologyBase(env, config) {
  // Server's POST /fetch lives next to /v1, not under /api/methodology.
  if (env.apiBase) return env.apiBase;
  const fromConfig = config?.api?.base_url;
  if (typeof fromConfig === "string" && fromConfig.trim()) {
    return stripSlash(fromConfig);
  }
  throw new Error("SHIP_API_BASE not set and no api.base_url in .ship/config.yml");
}


function splitFrontmatter(raw) {
  if (!raw.startsWith("---")) return { frontmatter: {}, body: raw };
  const end = raw.indexOf("\n---\n", 4);
  if (end < 0) return { frontmatter: {}, body: raw };
  const headRaw = raw.slice(3, end + 1).trim();
  const body = raw.slice(end + 5);
  let parsed = {};
  try {
    parsed = yaml.parse(headRaw) || {};
  } catch {
    parsed = {};
  }
  return { frontmatter: parsed, body };
}


function pickFsmStage(frontmatter) {
  const spec = frontmatter?.spec || {};
  const v = spec.fsm_stage ?? spec.fsmStage;
  if (typeof v === "string" && v.trim()) return v.trim();
  return null;
}


async function getNextTask({ apiBase, apiToken, workspaceId, repoId, state }) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/tracker/next?state=${encodeURIComponent(state)}`;
  const res = await fetch(url, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${apiToken}`,
    },
  });
  if (!res.ok) {
    throw new Error(`tracker/next ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  const body = await res.json();
  return body.ticket || null;
}


function renderPrompt({ patternBody, baseBody, role, routineSpec, task, fsmStage }) {
  const issueRef = task?.ticket_ref ? task.ticket_ref : "(no ticket)";
  const title = task?.title || "";
  const description = task?.body || "";

  // Pattern-template substitution. Order matters: expand {{BASE}} first
  // so any further {{ROLE}} / {{SKILLS_CONTEXT}} placeholders inside
  // common-base also get resolved.
  const baseExpanded = (baseBody || "")
    .replace(/\{\{ROLE\}\}/g, role)
    .replace(/\{\{ISSUE\}\}/g, issueRef)
    .replace(/\{\{SKILLS_CONTEXT\}\}/g, "(no skills directory bundled in this run)");

  const expanded = patternBody
    .replace(/\{\{BASE\}\}/g, baseExpanded)
    .replace(/\{\{ROLE\}\}/g, role)
    .replace(/\{\{ISSUE\}\}/g, issueRef)
    .replace(/\{\{TITLE\}\}/g, title.slice(0, 500))
    .replace(/\{\{DESCRIPTION\}\}/g, description.slice(0, 8000));

  const out = [];
  if (routineSpec.prompt) {
    out.push("## Routine instructions");
    out.push(routineSpec.prompt.trim());
    out.push("");
  }
  out.push(expanded.trim());
  if (task) {
    out.push("");
    out.push("## Task");
    out.push(`- **Ticket:** \`${task.ticket_ref}\` (${task.kind})`);
    if (task.url) out.push(`- **URL:** ${task.url}`);
    if (task.title) out.push(`- **Title:** ${task.title}`);
    if (task.fsm_stage || fsmStage) out.push(`- **FSM stage:** \`${task.fsm_stage || fsmStage}\``);
    if (Array.isArray(task.labels) && task.labels.length) {
      out.push(`- **Labels:** ${task.labels.join(", ")}`);
    }
    if (task.body) {
      out.push("");
      out.push("### Description");
      out.push(task.body);
    }
  }
  out.push("");
  out.push(EXIT_PROTOCOL);
  return out.join("\n");
}


async function fetchCommonBase(fetchBase) {
  try {
    const { content } = await fetchArtifact(fetchBase, "pattern", "common-base");
    return splitFrontmatter(content).body;
  } catch (err) {
    // common-base is optional — surface a warning but keep going.
    console.error(`warn: failed to fetch common-base pattern: ${err.message}`);
    return "";
  }
}


const EXIT_PROTOCOL = `## Required exit protocol

When you finish (or determine you cannot proceed), commit a single
file at \`.ship/run-state.json\` on this branch and stop.

The file MUST be valid JSON with this shape:

\`\`\`json
{
  "state": "ready_next_step" | "human_validation" | "blocked",
  "comment": "Markdown your work-product comment.",
  "transition_to": "<next FSM stage>",
  "payload": { "...optional structured details..." }
}
\`\`\`

State semantics:

- \`ready_next_step\` — your role is done. Set \`comment\` (what you did),
  \`transition_to\` (next FSM stage). Ship CLI will move the ticket and
  post the comment.
- \`human_validation\` — you need an answer from a human. Set \`comment\`
  with the question. Ship CLI will leave a comment on the ticket and
  drop a clarification item in the workspace inbox.
- \`blocked\` — you cannot proceed (missing secret, broken env). Set
  \`comment\` with the reason. Ship CLI will drop a blocker item in the
  inbox; the ticket stays where it is.

Do NOT call any tracker API directly (no \`gh issue comment\`, no
\`linear-cli\`, no curl to Ship). Ship CLI reads the state file after
you finish and does the writes through the workspace's existing OAuth.
`;


function makeBranchName(routine, ticketRef) {
  const stamp = Date.now().toString(36);
  if (ticketRef) {
    const safe = String(ticketRef).replace(/[^a-zA-Z0-9_-]/g, "-");
    return `cursor/ship-${routine}-${safe}-${stamp}`;
  }
  return `cursor/ship-${routine}-${stamp}`;
}


async function readBranchFile({ repo, branch, path: filePath, githubToken }) {
  if (!repo) throw new Error("readBranchFile: GITHUB_REPOSITORY is empty");
  if (!githubToken) throw new Error("readBranchFile: GITHUB_TOKEN is empty");
  const url = `https://api.github.com/repos/${repo}/contents/${encodeURIComponent(filePath)}?ref=${encodeURIComponent(branch)}`;
  const res = await fetch(url, {
    headers: {
      Accept: "application/vnd.github.raw",
      Authorization: `Bearer ${githubToken}`,
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!res.ok) {
    throw new Error(`GET ${filePath}@${branch} ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return res.text();
}


async function applyAgentState({
  apiBase,
  apiToken,
  workspaceId,
  repoId,
  routine,
  pattern,
  task,
  state,
  fsmStage,
}) {
  const actions = [];
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiToken}`,
  };
  const ws = encodeURIComponent(workspaceId);
  const repoSeg = encodeURIComponent(repoId);

  async function call(method, url, body) {
    const res = await fetch(url, { method, headers, body: body && JSON.stringify(body) });
    if (!res.ok) {
      throw new Error(`${method} ${url} ${res.status}: ${(await res.text()).slice(0, 300)}`);
    }
    return res.json().catch(() => ({}));
  }

  try {
    if (state.state === "ready_next_step") {
      if (!task?.ticket_ref) {
        throw new Error("ready_next_step requires a bound ticket");
      }
      if (!state.transition_to) {
        throw new Error("ready_next_step requires `transition_to`");
      }
      const r = await call(
        "POST",
        `${apiBase}/v1/workspaces/${ws}/repos/${repoSeg}/tracker/transition`,
        {
          ticket_ref: task.ticket_ref,
          to_state: state.transition_to,
          from_state: fsmStage || undefined,
          comment: state.comment || undefined,
        },
      );
      actions.push({ kind: "transition", to_state: state.transition_to, response: r });
      return { exitCode: EXIT_OK, statusName: "completed", actions };
    }
    if (state.state === "human_validation") {
      if (task?.ticket_ref && state.comment) {
        const c = await call(
          "POST",
          `${apiBase}/v1/workspaces/${ws}/repos/${repoSeg}/tracker/comment`,
          { ticket_ref: task.ticket_ref, body: state.comment },
        );
        actions.push({ kind: "comment", response: c });
      }
      const inbox = await call(
        "POST",
        `${apiBase}/v1/workspaces/${ws}/inbox/items`,
        {
          type: "clarification",
          title: `[${routine}] ${task?.title || "needs human"}`.slice(0, 300),
          summary: state.comment || null,
          ticket_ref: task?.ticket_ref || null,
          payload: {
            routine,
            pattern,
            agent_state: state.state,
            ...((state.payload && typeof state.payload === "object") ? state.payload : {}),
          },
        },
      );
      actions.push({ kind: "inbox", response: inbox });
      return { exitCode: EXIT_HUMAN, statusName: "human_validation", actions };
    }
    if (state.state === "blocked") {
      const inbox = await call(
        "POST",
        `${apiBase}/v1/workspaces/${ws}/inbox/items`,
        {
          type: "blocker",
          title: `[${routine}] blocked: ${task?.title || pattern}`.slice(0, 300),
          summary: state.comment || null,
          ticket_ref: task?.ticket_ref || null,
          payload: {
            routine,
            pattern,
            agent_state: state.state,
            ...((state.payload && typeof state.payload === "object") ? state.payload : {}),
          },
        },
      );
      actions.push({ kind: "inbox", response: inbox });
      return { exitCode: EXIT_BLOCKED, statusName: "blocked", actions };
    }
    throw new Error(
      `Unknown agent state '${state.state}' (expected ready_next_step | human_validation | blocked)`,
    );
  } catch (err) {
    return {
      exitCode: EXIT_API_FAIL,
      statusName: "error",
      actions: [...actions, { kind: "error", error: err instanceof Error ? err.message : String(err) }],
    };
  }
}


// ---------------------------------------------------------------------------
// Argument plumbing
// ---------------------------------------------------------------------------


function parseArgs(rest) {
  const out = { routine: null, cwd: null, json: false, help: false };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") { out.help = true; copy.shift(); continue; }
    if (a === "--json") { out.json = true; copy.shift(); continue; }
    if (a === "--routine" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--cwd" && copy[1] !== undefined) { out.cwd = path.resolve(copy[1]); copy.splice(0, 2); continue; }
    die(EXIT_USAGE, `unknown argument: ${a}`);
  }
  return out;
}


function printHelp() {
  console.log(`shipctl agent-run — execute one E14 routine end-to-end.

USAGE
  shipctl agent-run --routine <id> [--json] [--cwd <dir>]

ENV
  SHIP_API_BASE        Ship server base URL (e.g. https://ship.elmundi.com)
  SHIP_API_TOKEN       workspace API token (admin scope)
  SHIP_WORKSPACE_ID    UUID of the workspace
  SHIP_REPO_ID         UUID of the WorkspaceRepo row
  GITHUB_REPOSITORY    owner/repo (used for Cursor + state read)
  GITHUB_TOKEN         read-only repo token (to fetch .ship/run-state.json)
  CURSOR_API_KEY       Cursor Cloud API key (when agent.default.provider=cursor)

EXIT
  0  routine completed (or noop: no eligible ticket)
  1  usage / config error
  5  agent reported state=blocked
  6  agent reported state=human_validation
  7  agent runtime crashed / state file missing
  8  Ship API write failed
`);
}


function emit(args, payload) {
  if (args.json) {
    console.log(JSON.stringify(payload, null, 2));
  } else {
    console.error(`# ship: ${payload.status}${payload.reason ? ` reason=${payload.reason}` : ""}${payload.routine ? ` routine=${payload.routine}` : ""}`);
    if (payload.error) console.error(`#   error: ${payload.error}`);
  }
}


function die(code, msg) {
  console.error(msg);
  process.exit(code);
}
