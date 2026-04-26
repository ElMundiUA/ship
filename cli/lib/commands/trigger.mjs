import { readConfig } from "../config/io.mjs";
import { dueLanesFromRoutines, dueRoutines } from "../runtime/routines.mjs";

const VERSION = "v2";

export async function triggerCommand(ctx, rest) {
  const opts = parseArgs(rest);
  const { config } = readConfig(opts.cwd || process.cwd());
  const local = dueRoutines(config, { event: opts.event, now: opts.now ? new Date(opts.now) : new Date() });
  let due = local.due;

  const baseUrl = resolveBaseUrl(opts.baseUrl || explicitGlobalBaseUrl(ctx));
  const token = process.env.SHIP_API_TOKEN || "";
  let claimStatus = "skipped:no-token";
  if (token && due.length > 0 && !opts.noClaim) {
    let workspaceId = opts.workspace;
    if (!workspaceId) workspaceId = await resolveSoleWorkspace(baseUrl, token);
    const repoId = await resolveRepoId(baseUrl, token, workspaceId, opts.repo);
    const claimed = [];
    for (const routine of due) {
      const claim = await claimRoutine(baseUrl, token, workspaceId, repoId, opts.event, routine);
      if (claim.status === "claimed" || claim.status === "unavailable") {
        claimed.push({ ...routine, claim_status: claim.status });
      }
    }
    due = claimed;
    claimStatus = "attempted";
  }

  const result = {
    event: opts.event,
    status: due.length ? "due" : "noop",
    due_routines: due,
    due_lanes: dueLanesFromRoutines(due),
    skipped_routines: local.skipped,
    claim_status: claimStatus,
  };

  if (ctx.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (!due.length) {
    console.log(`Ship trigger ${opts.event}: no routines due.`);
    return;
  }
  console.log(`Ship trigger ${opts.event}: ${due.length} routine(s) due`);
  for (const routine of due) console.log(`  - ${routine.routine_id}`);
}

function explicitGlobalBaseUrl(ctx) {
  return ctx?.baseUrlSource === "flag" ? ctx.baseUrl : null;
}

function printHelp() {
  console.log(`shipctl trigger — compute which routines are due (${VERSION})

USAGE
  shipctl trigger --event schedule [--repo <id|owner/name>] [--workspace <id>] [--json]

ENV
  SHIP_API_TOKEN             Optional. When set, due routines are claimed in Ship.
  SHIP_WORKSPACE_API_BASE    Optional API base override.
  SHIP_API_BASE              Fallback API base override.
`);
}

function parseArgs(args) {
  const out = {
    event: null,
    workspace: null,
    repo: null,
    baseUrl: null,
    cwd: null,
    now: null,
    noClaim: false,
    json: false,
  };
  const copy = [...args];
  const consume = (flag, key) => {
    if (copy[0] === flag && copy[1] !== undefined) {
      copy.shift();
      out[key] = String(copy.shift());
      return true;
    }
    const p = `${flag}=`;
    if (copy[0] && copy[0].startsWith(p)) {
      out[key] = copy[0].slice(p.length);
      copy.shift();
      return true;
    }
    return false;
  };
  while (copy.length) {
    if (
      consume("--event", "event") ||
      consume("--workspace", "workspace") ||
      consume("--repo", "repo") ||
      consume("--base-url", "baseUrl") ||
      consume("--cwd", "cwd") ||
      consume("--now", "now")
    ) {
      continue;
    }
    if (copy[0] === "--no-claim") {
      out.noClaim = true;
      copy.shift();
      continue;
    }
    if (copy[0] === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (copy[0] === "--help" || copy[0] === "-h") {
      printHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.event) {
    console.error("Usage: shipctl trigger --event <schedule> --repo <id|owner/name>");
    process.exit(1);
  }
  if (!["schedule", "manual", "pull_request", "push"].includes(out.event)) {
    console.error("--event must be one of: schedule, manual, pull_request, push");
    process.exit(1);
  }
  return out;
}

function resolveBaseUrl(explicit) {
  if (explicit) return explicit.replace(/\/+$/, "");
  if (process.env.SHIP_WORKSPACE_API_BASE) return process.env.SHIP_WORKSPACE_API_BASE.replace(/\/+$/, "");
  if (process.env.SHIP_API_BASE) return process.env.SHIP_API_BASE.replace(/\/+$/, "");
  return "https://api.ship.elmundi.com";
}

async function resolveSoleWorkspace(baseUrl, token) {
  const rows = await apiGetJson(baseUrl, "/v1/workspaces", token);
  if (!Array.isArray(rows) || rows.length === 0) {
    console.error("No workspaces visible to this token.");
    process.exit(1);
  }
  if (rows.length > 1) {
    console.error("Token has access to more than one workspace; pass --workspace <id>.");
    process.exit(1);
  }
  return String(rows[0].id);
}

async function resolveRepoId(baseUrl, token, workspaceId, hint) {
  if (hint && /^[0-9a-fA-F-]{32,36}$/.test(hint) && hint.includes("-")) return hint;
  const rows = await apiGetJson(baseUrl, `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos`, token);
  if (!Array.isArray(rows) || rows.length === 0) {
    console.error(`Workspace ${workspaceId} has no activated repos.`);
    process.exit(1);
  }
  if (hint) {
    const match = rows.find((r) => r.full_name === hint || `${r.owner ?? ""}/${r.name ?? ""}` === hint || r.id === hint);
    if (!match) {
      console.error(`--repo ${hint} doesn't match any activated repo in workspace ${workspaceId}.`);
      process.exit(1);
    }
    return String(match.id);
  }
  return String(rows[0].id);
}

async function apiGetJson(baseUrl, path, token) {
  return apiRequest(baseUrl, path, "GET", token, null);
}

async function apiPostJson(baseUrl, path, body, token) {
  return apiRequest(baseUrl, path, "POST", token, body);
}

async function claimRoutine(baseUrl, token, workspaceId, repoId, event, routine) {
  try {
    return await apiPostJson(
      baseUrl,
      `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/routine-runs/claim`,
      {
        event,
        routine_id: routine.routine_id,
        window_key: routine.window_key,
        scheduled_for: routine.scheduled_for,
        window_start: routine.window_start,
        window_end: routine.window_end,
        github: {
          event_name: process.env.SHIP_EVENT_NAME || process.env.GITHUB_EVENT_NAME || "",
          ref: process.env.SHIP_REF || process.env.GITHUB_REF || "",
          sha: process.env.SHIP_SHA || process.env.GITHUB_SHA || "",
          run_id: process.env.GITHUB_RUN_ID || "",
        },
      },
      token,
    );
  } catch (err) {
    console.error(`warn: routine claim failed, running locally: ${err instanceof Error ? err.message : err}`);
    return { status: "unavailable" };
  }
}

async function apiRequest(baseUrl, path, method, token, body) {
  const url = `${baseUrl}${path}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    console.error(`Network error calling ${url}: ${err instanceof Error ? err.message : err}`);
    process.exit(3);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.ok) return data;
  const msg = typeof data === "string" ? data : JSON.stringify(data);
  throw new Error(`HTTP ${res.status} ${res.statusText} on ${method} ${url}\n${msg}`);
}
