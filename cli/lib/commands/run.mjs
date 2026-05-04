/**
 * `shipctl run` — E14 routine entry-point.
 *
 * Customer's GH Actions cron fires this once per routine slot. The
 * pipeline:
 *
 *   1. Read the routine from `.ship/config.yml` (specialist slug +
 *      optional inline prompt).
 *   2. Resolve the agent role body via the workspace endpoint
 *      `GET /v1/workspaces/{ws}/agent-roles/{slug}/resolve` —
 *      workspace overrides win, otherwise the Ship default. Pull
 *      the `system` (shared base) body in parallel.
 *   3. If the resolved role declares `fsm_stage`, ask Ship server
 *      for the next ticket in that stage
 *      (`GET /v1/.../tracker/next?state=<stage>`). Server picks the
 *      adapter (Linear / GH Issues / etc.) — CLI doesn't care.
 *   4. Mint a `run_id` and render the prompt: system body + role
 *      body + routine prompt + ticket details + a finish-protocol
 *      block with `SHIP_API_BASE`, `SHIP_API_TOKEN`, `SHIP_WORKSPACE_ID`,
 *      `RUN_ID`, `TICKET_REF`, `FSM_STAGE` already substituted so the
 *      agent can call the finish endpoint directly.
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

import { readConfig, findShipRoot } from "../config/io.mjs";
import { resolveExecutable } from "../runtime/routines.mjs";
import { resolveProvider, runAgent } from "../agents/index.mjs";

const SYSTEM_ROLE_SLUG = "system";


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
  if (!args.routine && !args.specialist) {
    die(
      EXIT_USAGE,
      "either `--routine <id>` or `--specialist <slug>` is required.\nRun: shipctl run --help",
    );
  }
  if (args.routine && args.specialist) {
    die(EXIT_USAGE, "`--routine` and `--specialist` are mutually exclusive.");
  }

  const cwd = args.cwd || process.cwd();
  const root = findShipRoot(cwd);
  if (!root) {
    die(EXIT_USAGE, `.ship/config.yml not found (searched from ${path.resolve(cwd)} upward).`);
  }

  const { config } = readConfig(cwd);
  // Routine mode: resolve from ``.ship/config.yml``. Specialist mode
  // (used by the pipeline-pick fallback in the trigger workflow):
  // synthesize a minimal executable so the rest of the pipeline can
  // stay routine-shaped without inventing a ``pipeline:<slug>``
  // routine in the YAML.
  let resolved;
  if (args.specialist) {
    resolved = {
      kind: "specialist",
      id: args.specialist,
      source: { specialist: args.specialist },
      executable: {
        id: args.specialist,
        type: "specialist",
        kind: "pipeline_pick",
        specialist: args.specialist,
        prompt: null,
      },
    };
  } else {
    resolved = resolveExecutable(config, args.routine);
    if (!resolved) {
      die(EXIT_USAGE, `unknown routine '${args.routine}' in .ship/config.yml`);
    }
  }

  // ``runId`` for emit/branch naming carries either the routine id or
  // the specialist slug. Logging downstream uses ``runHandle``.
  const runHandle = args.routine || `pipeline:${args.specialist}`;

  const env = readEnv();
  const { apiBase, apiToken, workspaceId, githubRepo } = env;

  // 1) Resolve specialist slug.
  // ``routine.specialist`` is the canonical Phase-2.4 form; the legacy
  // ``routine.pattern`` is mapped to a slug in
  // ``cli/lib/runtime/routines.mjs`` (drops the ``role-`` prefix).
  const specialistSlug = resolved.executable.specialist || args.specialist;
  if (!specialistSlug) {
    die(
      EXIT_USAGE,
      `routine '${args.routine}' has no 'specialist:' (or legacy 'pattern:') set`,
    );
  }
  if (!apiBase || !apiToken || !workspaceId) {
    die(
      EXIT_USAGE,
      "agent-role resolve requires SHIP_API_BASE + SHIP_API_TOKEN + SHIP_WORKSPACE_ID",
    );
  }

  // 2) Pull the resolved role + the shared system prompt in parallel.
  // ``resolveAgentRole`` returns the workspace override when present,
  // otherwise falls back to the Ship default. ``system`` is fetched
  // optional — older deployments may not have it; we render without
  // a system header in that case.
  const [roleResolved, systemResolved] = await Promise.all([
    resolveAgentRole({ apiBase, apiToken, workspaceId, slug: specialistSlug }),
    resolveAgentRole({
      apiBase,
      apiToken,
      workspaceId,
      slug: SYSTEM_ROLE_SLUG,
      optional: true,
    }),
  ]);
  if (!roleResolved) {
    die(EXIT_USAGE, `unknown agent role '${specialistSlug}' for this workspace`);
  }
  const fsmStage = roleResolved.fsm_stage || null;
  const roleBody = roleResolved.prompt || "";
  const systemBody = systemResolved?.prompt || "";

  // 3) Resolve task. ``--dry-run`` skips the server call and uses a
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
        emit(args, {
          status: "noop",
          routine: args.routine,
          specialist: specialistSlug,
          fsm_stage: fsmStage,
          reason: "no_eligible_ticket",
          run_handle: runHandle,
        });
        process.exit(EXIT_NO_TASK);
      }
    }
  }

  // 4) Fetch the workspace policy preamble so the agent prompt
  // carries the same standing rules the Navigator chat does. Best-
  // effort: a missing token, missing API base, or a network failure
  // quietly skips the prepend — local / offline runs still work.
  // ``role`` is now the specialist slug directly (no more
  // ``spec.role`` indirection).
  const policiesPreamble = await fetchPoliciesPreamble({
    apiBase,
    apiToken,
    workspaceId,
    role: specialistSlug,
  });

  // 5) Mint a run_id + render prompt with finish-protocol values
  // already substituted so the agent can call /agent-runs/finish from
  // inside Cursor without holding any extra config.
  const runId = `run_${crypto.randomBytes(8).toString("hex")}`;
  const renderedPrompt = renderPrompt({
    patternBody: roleBody,
    baseBody: systemBody,
    role: specialistSlug,
    routineSpec: resolved.executable,
    task,
    fsmStage,
    finishCtx: {
      apiBase,
      apiToken,
      workspaceId,
      runId,
      role: specialistSlug,
      ticketRef: task?.ticket_ref || null,
      fsmStage: fsmStage || null,
    },
  });
  const prompt = policiesPreamble
    ? `${policiesPreamble.trim()}\n\n---\n\n${renderedPrompt}`
    : renderedPrompt;

  // ``--dry-run`` exits here so the operator can eyeball the rendered
  // prompt + resolved task without launching an agent or touching any
  // tracker. Useful when iterating on prompts.
  if (args.dryRun || ctx.dryRun) {
    if (args.json) {
      console.log(JSON.stringify({
        status: "dry-run",
        routine: args.routine,
        specialist: specialistSlug,
        specialist_source: roleResolved.source,
        fsm_stage: fsmStage,
        task,
        prompt,
      }, null, 2));
    } else {
      console.error(`# ship: dry-run handle=${runHandle} specialist=${specialistSlug} (${roleResolved.source}) fsm_stage=${fsmStage || "(context-free)"}`);
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
  const provider = resolveProvider(config, args.routine || specialistSlug);
  const branchName = makeBranchName(runHandle, task?.ticket_ref);
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
      specialist: specialistSlug,
      run_id: runId,
      run_handle: runHandle,
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
    specialist: specialistSlug,
    fsm_stage: fsmStage,
    ticket_ref: task?.ticket_ref || null,
    agent_id: runtime.agentId,
    branch: runtime.branchName,
    cursor_status: runtime.status,
    run_id: runId,
    run_handle: runHandle,
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


/**
 * Resolve an agent role through the workspace endpoint.
 *
 * Returns ``{slug, name, prompt, fsm_stage, source}`` (workspace row
 * or Ship default), ``null`` for 404, throws on auth/network errors
 * unless ``optional`` is set (then 404 *and* errors return ``null``
 * with a one-line warning).
 */
async function resolveAgentRole({
  apiBase,
  apiToken,
  workspaceId,
  slug,
  optional = false,
}) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}/resolve`;
  let res;
  try {
    res = await fetch(url, {
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${apiToken}`,
      },
    });
  } catch (err) {
    if (optional) {
      console.error(
        `warn: agent-role '${slug}' resolve failed (network): ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
    throw err;
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    if (optional) {
      console.error(
        `warn: agent-role '${slug}' resolve returned ${res.status}; running without it`,
      );
      return null;
    }
    throw new Error(
      `agent-roles resolve ${res.status}: ${(await res.text()).slice(0, 300)}`,
    );
  }
  return res.json();
}


/**
 * Best-effort fetch of the workspace's policy preamble. Returns the
 * markdown block to prepend, or ``null`` when there's nothing to
 * inject (no policies, missing token / API base, network error,
 * non-200 response). Never throws — a broken policies path mustn't
 * break a routine run.
 *
 * Auth: workspace-membership token (``SHIP_API_TOKEN`` — same one
 * the rest of ``run.mjs`` uses). The companion run-token endpoint
 * at ``/v1/pipelines/runs/{run_id}/policies-preamble`` doesn't fit
 * this flow because the CLI mints ``run_id`` locally; the
 * workspace-scoped variant takes membership instead.
 */
async function fetchPoliciesPreamble({ apiBase, apiToken, workspaceId, role }) {
  if (!apiBase || !apiToken || !workspaceId) return null;
  const qs = role ? `?role=${encodeURIComponent(role)}` : "";
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/policies/preamble${qs}`;
  let res;
  try {
    res = await fetch(url, {
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${apiToken}`,
      },
    });
  } catch (err) {
    console.error(
      `warn: policies preamble fetch failed (network): ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
  if (!res.ok) {
    // 404 happens against older backends that don't have the
    // workspace-scoped endpoint yet — silent skip there. Other
    // statuses (401, 403, 500) get a one-line warning so operators
    // notice misconfigurations without aborting the run.
    if (res.status !== 404) {
      console.error(
        `warn: policies preamble fetch returned ${res.status}; running without preamble`,
      );
    }
    return null;
  }
  let body;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  return typeof body?.preamble === "string" && body.preamble.trim()
    ? body.preamble
    : null;
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
  out.push("");
  out.push(renderLifecycleHooks());
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


function renderLifecycleHooks() {
  // Phase 4: every run is bracketed by two auditable lifecycle hooks
  // — knowledge-fetch first, knowledge-feedback last. We render them
  // as explicit prompt instructions so they show up in the agent's
  // tool-use log (the runner audits the log to flag runs that
  // skipped them). Soft for now (no run-blocking enforcement) but
  // the prompt is the canonical contract until the runner grows
  // tool-stream interception.
  return [
    "## Lifecycle hooks (Phase 4)",
    "",
    "**First call — knowledge fetch.** Before any other tool call,",
    `run \`shipctl knowledge fetch <bucket>\` for at least one bucket`,
    "relevant to your role. The audit log flags runs whose first",
    "tool call wasn't a knowledge fetch; do not skip this to save",
    "tokens.",
    "",
    "**Last call — knowledge feedback.** Before calling the finish",
    "endpoint, leave one-line learnings via the Ship knowledge",
    "feedback channel (`shipctl feedback draft` → `feedback submit`)",
    "if you discovered something a future run on this codebase would",
    "want to know. Empty findings are a valid outcome — better no",
    "feedback than fabricated polish.",
  ].join("\n");
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
  "description": "<Full rewritten ticket body in Markdown — Problem / Goal / Acceptance criteria / Scope / Non-goals / Risks / etc. — when your role's job is to shape the ticket itself (intake, BA, planner). Omit (null) when your role is not supposed to rewrite the body.>",
  "comment": "<One-paragraph audit narration of what you changed and why, ending with [Ship SDLC:${ctx?.role || "{{ROLE}}"}]. Do NOT paste the new description here — that's what the description field is for.>",
  "summary": null,
  "payload": {}
}
JSON
\`\`\`

### Outcomes

- **\`ready_next_step\`** — your role finished cleanly. Two shapes:

  1. **You worked on a ticket.** Set \`ticket_ref\` and \`stage_next\`
     to the next FSM stage; server moves the ticket. If your role's
     job is to shape the ticket (intake / BA / planner), set
     \`description\` to the **full rewritten body** — the server
     replaces the tracker description (Linear keeps the prior body
     in the activity feed, so nothing is lost). Use \`comment\` for
     a short audit narration of what changed and why; **do not put
     the new spec text in a comment**, otherwise the ticket
     description rots while comments accumulate. Pure-narration
     roles (security-officer, retro) skip \`description\` entirely.
  2. **There was nothing to do.** Pass \`ticket_ref: null\` and omit
     \`stage_next\`. The server records the run in the audit log and
     does **nothing** else — no inbox row, no tracker mutation. This
     is the right outcome when a context-free routine (daily audit,
     security sweep, retro) found no findings, OR when an FSM-stage
     agent picked up no eligible ticket. **No work is not a blocker.**

- **\`needs_clarification\`** — you're waiting on a human. Set
  \`comment\` with the question (server posts it) or omit it if you
  already left the question via a separate read-only path. Server
  tags the ticket \`needs:clarification\` so intake stops re-picking.
  Status stays Todo. \`stage_next\` is ignored. Requires a
  \`ticket_ref\`.

- **\`blocked\`** — **the environment is broken.** Use this only when
  something on the runner side prevents the work from running:
  missing secret, dead adapter, conflicting branch, tracker
  unreachable, snyk/probe binary not installed, etc. Server drops a
  blocker row into the inbox so an operator can fix the plumbing.
  **Do not use \`blocked\` to mean "no findings" or "queue empty"**
  — those are \`ready_next_step\` with \`ticket_ref: null\`.

- **\`out_of_scope\`** — the ticket is invalid or shouldn't be
  processed. Server moves it to Done with optional \`comment\`.
  \`stage_next\` is ignored. Requires a \`ticket_ref\`.

### Security

\`SHIP_API_TOKEN\` is rendered into this prompt so you can call the
finish endpoint. **Do not echo it back into commit messages, PR
descriptions, comments, logs, or any output you produce.** Treat it
as a one-shot credential for this run.
`;
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
  const out = {
    routine: null,
    specialist: null,
    cwd: null,
    json: false,
    help: false,
    dryRun: false,
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") { out.help = true; copy.shift(); continue; }
    if (a === "--json") { out.json = true; copy.shift(); continue; }
    if (a === "--dry-run") { out.dryRun = true; copy.shift(); continue; }
    if (a === "--routine" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--specialist" && copy[1] !== undefined) { out.specialist = copy[1]; copy.splice(0, 2); continue; }
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
  console.log(`shipctl run — execute one E14 routine or pipeline-pick specialist end-to-end.

Run
  shipctl run --routine <id> [--json] [--cwd <dir>] [--dry-run]
  shipctl run --specialist <slug> [--json] [--cwd <dir>] [--dry-run]

  --routine     id from process.routines in .ship/config.yml (cron-driven)
  --specialist  agent-role slug from the Ship registry (pipeline-pick fallback)

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
