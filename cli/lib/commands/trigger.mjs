import { readConfig } from "../config/io.mjs";

const VERSION = "v1";

export async function triggerCommand(ctx, rest) {
  const opts = parseArgs(rest);
  const baseUrl = resolveBaseUrl(opts.baseUrl || ctx.baseUrl);
  const token = requireToken();
  let workspaceId = opts.workspace;
  if (!workspaceId) workspaceId = await resolveSoleWorkspace(baseUrl, token);
  const repoId = await resolveRepoId(baseUrl, token, workspaceId, opts.repo);
  const { config } = readConfig(opts.cwd || process.cwd());

  const result = await apiPostJson(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/repos/${encodeURIComponent(repoId)}/trigger`,
    {
      event: opts.event,
      config,
      github: {
        event_name: process.env.SHIP_EVENT_NAME || process.env.GITHUB_EVENT_NAME || "",
        ref: process.env.SHIP_REF || process.env.GITHUB_REF || "",
        sha: process.env.SHIP_SHA || process.env.GITHUB_SHA || "",
        run_id: process.env.GITHUB_RUN_ID || "",
      },
    },
    token,
  );

  if (ctx.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  const due = Array.isArray(result.due_lanes) ? result.due_lanes : [];
  if (!due.length) {
    console.log(`Ship trigger ${opts.event}: no lanes due.`);
    return;
  }
  console.log(`Ship trigger ${opts.event}: ${due.length} lane(s) due`);
  for (const lane of due) console.log(`  - ${lane.lane_id}`);
}

function printHelp() {
  console.log(`shipctl trigger — ask Ship which lanes are due (${VERSION})

USAGE
  shipctl trigger --event schedule --repo <id|owner/name> [--workspace <id>] [--json]

ENV
  SHIP_API_TOKEN             Required.
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
      consume("--cwd", "cwd")
    ) {
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

function requireToken() {
  const token = process.env.SHIP_API_TOKEN || "";
  if (!token) {
    console.error("SHIP_API_TOKEN is required.");
    process.exit(1);
  }
  return token;
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
  console.error(`HTTP ${res.status} ${res.statusText} on ${method} ${url}\n${msg}`);
  process.exit(res.status >= 500 ? 3 : 1);
}
