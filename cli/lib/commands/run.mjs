/**
 * `shipctl run` — E14 routine entry-point.
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
 *   4. Mint a `run_id` and render the prompt: pattern body + ticket
 *      details + a finish-protocol block with `SHIP_API_BASE`,
 *      `SHIP_API_TOKEN`, `SHIP_WORKSPACE_ID`, `RUN_ID`, `TICKET_REF`,
 *      `FSM_STAGE` already substituted so the agent can call the
 *      finish endpoint directly.
 *   5. Launch the configured agent runtime (`cli/lib/agents/`) — Cursor
 *      Cloud today. Block until the runtime terminates.
 *   6. The agent itself calls
 *      `POST /v1/workspaces/{ws}/agent-runs/finish` with its outcome.
 *      Ship's server applies tracker side-effects via the workspace
 *      Linear OAuth — the CLI doesn't read any branch / state file.
 *   7. CLI exits 0 on Cursor `FINISHED`, non-0 if the runtime crashed.
 *      Whether the agent actually called `/finish` is observable via
 *      the audit log; the smoke test for "did the right thing happen"
 *      is the tracker label/state itself.
 *
 * Env contract (typically wired in `ship-trigger-schedule.yml`):
 *   - SHIP_API_BASE         — Ship server, e.g. https://api.ship.elmundi.com
 *   - SHIP_API_TOKEN        — workspace API token (admin scope; rendered
 *                             into the agent's prompt so it can call
 *                             /agent-runs/finish from inside Cursor)
 *   - SHIP_WORKSPACE_ID     — UUID of the workspace this run belongs to
 *   - CURSOR_API_KEY        — Cursor Cloud agent API key (when provider=cursor)
 *   - GITHUB_REPOSITORY     — owner/repo (the repo the agent will check out)
 *
 * Note: there is **no SHIP_REPO_ID** — a workspace is the project, so
 * the tracker is workspace-scoped. ``GITHUB_REPOSITORY`` only tells the
 * agent runtime which checkout to spawn for code work. Branchless agents
 * (intake, BA, planner) don't push commits at all.
 */

import crypto from "node:crypto";
import path from "node:path";

import yaml from "yaml";

import { readConfig, findShipRoot } from "../config/io.mjs";
import { resolveExecutable } from "../runtime/routines.mjs";
import { fetchArtifact } from "../http.mjs";
import { readArtifactFile } from "../artifacts/fs-index.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { resolveProvider, runAgent } from "../agents/index.mjs";


const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_NO_TASK = 0; // intentional: no eligible ticket is a clean noop
const EXIT_AGENT_FAIL = 7;


// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------


export async function runCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }
  if (!args.routine) {
    die(EXIT_USAGE, "`--routine <id>` is required.\nRun: shipctl run --help");
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
  const { apiBase, apiToken, workspaceId, githubRepo } = env;

  // 1) Resolve pattern
  const patternId = resolved.executable.pattern;
  if (!patternId) {
    die(EXIT_USAGE, `routine '${args.routine}' has no pattern set`);
  }

  const fetchBase = methodologyBase(env, config);
  const rawPatternBody = await loadPattern({ id: patternId, fetchBase });
  const { frontmatter, frontmatterRaw, body: patternBody } = splitFrontmatter(rawPatternBody);
  const fsmStage = pickFsmStage(frontmatter, frontmatterRaw);

  // Patterns reference ``{{BASE}}`` to splice in common-base. Fetch it
  // up-front so renderPrompt can do the substitution. ``{{SKILLS_CONTEXT}}``
  // inside common-base is left as "(no skills directory)" for the MVP —
  // skills bundling lands in a follow-up.
  const baseRaw = await loadPattern({ id: "common-base", fetchBase, optional: true });
  const baseBody = baseRaw ? splitFrontmatter(baseRaw).body : "";

  // 2) Resolve task. ``--dry-run`` skips the server call and uses a
  // synthetic task so the operator can see the prompt shape without
  // needing the new endpoints deployed.
  let task = null;
  if (fsmStage) {
    if (args.dryRun || ctx.dryRun) {
      task = {
        ticket_ref: "dry-run/sample#1",
        kind: "dry-run",
        title: "Sample ticket for dry-run prompt rendering",
        body: "This is a synthetic ticket body. The real one comes from `GET /tracker/next` when not in dry-run.",
        url: null,
        labels: ["sample"],
        state: "open",
        fsm_stage: fsmStage,
      };
    } else {
      task = await getNextTask({
        apiBase,
        apiToken,
        workspaceId,
        state: fsmStage,
      });
      if (!task) {
        emit(args, { status: "noop", routine: args.routine, pattern: patternId, fsm_stage: fsmStage, reason: "no_eligible_ticket" });
        process.exit(EXIT_NO_TASK);
      }
    }
  }

  // 3) Mint a run_id + render prompt with finish-protocol values
  // already substituted so the agent can call /agent-runs/finish from
  // inside Cursor without holding any extra config.
  const runId = `run_${crypto.randomBytes(8).toString("hex")}`;
  const prompt = renderPrompt({
    patternBody,
    baseBody,
    role: patternId,
    routineSpec: resolved.executable,
    task,
    fsmStage,
    finishCtx: {
      apiBase,
      apiToken,
      workspaceId,
      runId,
      role: patternId,
      ticketRef: task?.ticket_ref || null,
      fsmStage: fsmStage || null,
    },
  });

  // ``--dry-run`` exits here so the operator can eyeball the rendered
  // prompt + resolved task without launching an agent or touching any
  // tracker. Useful when iterating on pattern bodies.
  if (args.dryRun || ctx.dryRun) {
    if (args.json) {
      console.log(JSON.stringify({
        status: "dry-run",
        routine: args.routine,
        pattern: patternId,
        fsm_stage: fsmStage,
        task,
        prompt,
      }, null, 2));
    } else {
      console.error(`# ship: dry-run routine=${args.routine} pattern=${patternId} fsm_stage=${fsmStage || "(context-free)"}`);
      if (task) {
        console.error(`# ship: task ticket_ref=${task.ticket_ref} title=${JSON.stringify(task.title || "")}`);
      } else {
        console.error("# ship: task=(none)");
      }
      console.error("# ---- prompt ----");
      console.log(prompt);
    }
    process.exit(EXIT_OK);
  }

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
      run_id: runId,
      stage: "launch_agent",
      error: err instanceof Error ? err.message : String(err),
    });
    process.exit(EXIT_AGENT_FAIL);
  }

  // The agent calls POST /agent-runs/finish from inside Cursor with
  // its outcome. The CLI's job ends here — Cursor's terminal status
  // tells us the runtime didn't crash; whether the agent actually
  // called /finish (and what outcome it reported) is observable in
  // the audit log + the resulting tracker state.
  emit(args, {
    status: "completed",
    routine: args.routine,
    pattern: patternId,
    fsm_stage: fsmStage,
    ticket_ref: task?.ticket_ref || null,
    agent_id: runtime.agentId,
    branch: runtime.branchName,
    cursor_status: runtime.status,
    run_id: runId,
  });
  process.exit(EXIT_OK);
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function readEnv() {
  return {
    apiBase: stripSlash(process.env.SHIP_API_BASE || ""),
    apiToken: process.env.SHIP_API_TOKEN || "",
    workspaceId: process.env.SHIP_WORKSPACE_ID || "",
    githubRepo: process.env.GITHUB_REPOSITORY || "",
    githubRef: (process.env.GITHUB_REF_NAME || "main").trim(),
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
  if (!raw.startsWith("---")) return { frontmatter: {}, frontmatterRaw: "", body: raw };
  const end = raw.indexOf("\n---\n", 4);
  if (end < 0) return { frontmatter: {}, frontmatterRaw: "", body: raw };
  const headRaw = raw.slice(3, end + 1);
  const body = raw.slice(end + 5);
  let parsed = {};
  try {
    parsed = yaml.parse(headRaw) || {};
  } catch {
    // Some Ship patterns have unquoted ``@elmundi/ship-core`` in
    // ``authors`` which strict YAML rejects. The CLI doesn't need the
    // full document — ``pickFsmStage`` falls back to a regex on the
    // raw frontmatter text in that case.
    parsed = {};
  }
  return { frontmatter: parsed, frontmatterRaw: headRaw, body };
}


function pickFsmStage(frontmatter, frontmatterRaw) {
  const spec = frontmatter?.spec || {};
  const v = spec.fsm_stage ?? spec.fsmStage;
  if (typeof v === "string" && v.trim()) return v.trim();
  if (frontmatterRaw) {
    // Strict-YAML-fallback: regex on the raw frontmatter. Matches
    // ``fsm_stage: triage`` (with optional surrounding whitespace, with
    // or without quotes) anywhere in the spec block.
    const m = frontmatterRaw.match(/^\s*fsm_stage:\s*['"]?([\w.-]+)['"]?/m);
    if (m && m[1]) return m[1].trim();
  }
  return null;
}


async function getNextTask({ apiBase, apiToken, workspaceId, state }) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker/next?state=${encodeURIComponent(state)}`;
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


function renderPrompt({ patternBody, baseBody, role, routineSpec, task, fsmStage, finishCtx }) {
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
  out.push(renderExitProtocol(finishCtx));
  return out.join("\n");
}


function renderExitProtocol(ctx) {
  // Substitute the run-time values directly into the example so the
  // agent doesn't have to figure out env var hookup. The token is
  // workspace-scoped and meant for this run; the prompt warns the
  // agent not to echo it.
  const apiBase = ctx?.apiBase || "$SHIP_API_BASE";
  const apiToken = ctx?.apiToken || "$SHIP_API_TOKEN";
  const workspaceId = ctx?.workspaceId || "$SHIP_WORKSPACE_ID";
  const runId = ctx?.runId || "<run_id>";
  const ticketRef = ctx?.ticketRef ?? null;
  const fsm = ctx?.fsmStage ?? null;
  const ticketLine = ticketRef === null
    ? '"ticket_ref": null,'
    : `"ticket_ref": ${JSON.stringify(ticketRef)},`;
  const fsmLine = fsm === null
    ? '"fsm_stage": null,'
    : `"fsm_stage": ${JSON.stringify(fsm)},`;

  return `## Required exit protocol

When you finish (or determine you cannot proceed), call Ship's finish
endpoint **once** with your outcome and stop. This is the only
sanctioned write surface — Ship's server applies tracker side-effects
through the workspace's existing OAuth.

**Do not** create empty branches or commit placeholder files. If your
role doesn't change code, no branch is required. If your role does
change code, push the code on the branch Ship CLI named for you, then
call finish.

**Do not** call any Linear / Jira / GitHub MCP that writes. Reading
via MCP is fine; writing is not. The finish endpoint is the only
write surface.

\`\`\`bash
curl -fsS -X POST '${apiBase}/v1/workspaces/${workspaceId}/agent-runs/finish' \\
  -H 'Authorization: Bearer ${apiToken}' \\
  -H 'Content-Type: application/json' \\
  --data @- <<'JSON'
{
  "run_id": ${JSON.stringify(runId)},
  "outcome": "ready_next_step",
  ${ticketLine}
  ${fsmLine}
  "stage_next": "<next FSM stage, e.g. ba_requirements>",
  "comment": "Markdown summary of what you did. End with [Ship SDLC:${ctx?.role || "{{ROLE}}"}].",
  "summary": null,
  "payload": {}
}
JSON
\`\`\`

### Outcomes

- **\`ready_next_step\`** — your role finished cleanly. Set
  \`stage_next\` to the next FSM stage. Server moves the ticket and
  posts \`comment\` if provided.
- **\`needs_clarification\`** — you're waiting on a human. Set
  \`comment\` with the question (server posts it) or omit it if you
  already left the question via a separate read-only path. Server
  tags the ticket \`needs:clarification\` so intake stops re-picking.
  Status stays Todo. \`stage_next\` is ignored.
- **\`blocked\`** — you cannot proceed (missing secret, broken env,
  conflicting branch). Server drops a blocker into the workspace
  inbox; ticket unchanged. \`stage_next\` is ignored.
- **\`out_of_scope\`** — the ticket is invalid or shouldn't be
  processed. Server moves it to Done with optional \`comment\`.
  \`stage_next\` is ignored.

### Security

\`SHIP_API_TOKEN\` is rendered into this prompt so you can call the
finish endpoint. **Do not echo it back into commit messages, PR
descriptions, comments, logs, or any output you produce.** Treat it
as a one-shot credential for this run.
`;
}


/**
 * Load a pattern body. Resolution order:
 *   1) when running inside the Ship monorepo, read from
 *      ``artifacts/patterns/<id>/ARTIFACT.md`` on disk — fast and
 *      always reflects the working tree (good for dry-runs / local
 *      smoke tests before the server is rebuilt).
 *   2) otherwise hit the server's ``POST /fetch``.
 */
async function loadPattern({ id, fetchBase, optional = false }) {
  const shipRepo = resolveShipRepoRootForCatalog();
  if (shipRepo) {
    const file = readArtifactFile(shipRepo, "pattern", id);
    if (file && typeof file.content === "string") return file.content;
  }
  try {
    const { content } = await fetchArtifact(fetchBase, "pattern", id);
    return content;
  } catch (err) {
    if (optional) {
      console.error(`warn: failed to fetch pattern '${id}': ${err.message}`);
      return "";
    }
    throw err;
  }
}


function makeBranchName(routine, ticketRef) {
  const stamp = Date.now().toString(36);
  if (ticketRef) {
    const safe = String(ticketRef).replace(/[^a-zA-Z0-9_-]/g, "-");
    return `cursor/ship-${routine}-${safe}-${stamp}`;
  }
  return `cursor/ship-${routine}-${stamp}`;
}


// ---------------------------------------------------------------------------
// Argument plumbing
// ---------------------------------------------------------------------------


function parseArgs(rest) {
  const out = { routine: null, cwd: null, json: false, help: false, dryRun: false };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") { out.help = true; copy.shift(); continue; }
    if (a === "--json") { out.json = true; copy.shift(); continue; }
    if (a === "--dry-run") { out.dryRun = true; copy.shift(); continue; }
    if (a === "--routine" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--cwd" && copy[1] !== undefined) { out.cwd = path.resolve(copy[1]); copy.splice(0, 2); continue; }
    // Soft-ignore legacy flags that older trigger workflows still pass —
    // the new pipeline doesn't need them and refusing would break repos
    // that haven't re-seeded yet. ``--lane`` is the back-compat spelling
    // of ``--routine`` from before the rename.
    if (a === "--trigger" && copy[1] !== undefined) { copy.splice(0, 2); continue; }
    if (a === "--lane" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    die(EXIT_USAGE, `unknown argument: ${a}`);
  }
  return out;
}


function printHelp() {
  console.log(`shipctl run — execute one E14 routine end-to-end.

Run
  shipctl run --routine <id> [--json] [--cwd <dir>] [--dry-run]

ENV
  SHIP_API_BASE        Ship server base URL (e.g. https://api.ship.elmundi.com)
  SHIP_API_TOKEN       workspace API token; rendered into the agent prompt
                       so the agent can call /agent-runs/finish itself
  SHIP_WORKSPACE_ID    UUID of the workspace (a workspace is one project)
  GITHUB_REPOSITORY    owner/repo (which checkout the agent gets)
  CURSOR_API_KEY       Cursor Cloud API key (when agent.default.provider=cursor)

EXIT
  0  agent runtime reached a terminal state (FINISHED/CANCELLED/ERRORED).
     Whether the agent actually called /agent-runs/finish is observable
     in the audit log — this CLI no longer waits on that signal.
  1  usage / config error
  7  agent runtime failed to launch
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
