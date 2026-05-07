/**
 * `shipctl tracker` — workspace-scoped ticket reads + writes.
 *
 * Reviewer routines (tech-reviewer / qa-reviewer / security-officer)
 * file findings via this CLI instead of poking Linear MCP directly,
 * because Cursor's MCP often holds a different organisation's PAT
 * than the workspace under audit. Going through Ship routes the
 * mutation through the workspace's bound OAuth, so writes always
 * land in the right inbox.
 *
 * Subcommands:
 *
 *   shipctl tracker create-ticket
 *     --project-id <linear-project-uuid>
 *     --title <s>
 *     --body  <md> | --body-file <path|->
 *     [--labels a,b,c]
 *     [--priority 0..4]      Linear: 0=No priority, 1=Urgent,
 *                            2=High, 3=Medium, 4=Low
 *     [--workspace <id>] [--json]
 *
 *   shipctl tracker list-project-tickets
 *     --project-id <linear-project-uuid>
 *     [--open-only true|false]   default: true
 *     [--limit N]                default 100, max 250
 *     [--workspace <id>] [--json]
 *
 * Auth: bearer ``SHIP_API_TOKEN``.
 *
 * Exit codes:
 *   0  ticket created / list returned
 *   1  arg / config / 4xx error
 *   2  auth error (401)
 *   3  network / 5xx error
 */

import {
  apiRequest,
  consumeBodyFlags,
  consumeStringFlag,
  readBodyFromOpts,
  resolveContext,
} from "../agent_api.mjs";


export async function trackerCommand(ctx, rest) {
  const [sub, ...args] = rest;
  if (!sub || sub === "help" || sub === "-h" || sub === "--help") {
    printTrackerHelp();
    return;
  }
  if (sub === "create-ticket") {
    await trackerCreateTicketCommand(ctx, args);
    return;
  }
  if (sub === "list-project-tickets") {
    await trackerListProjectTicketsCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl tracker' subcommand: ${sub}\nRun: shipctl tracker --help`,
  );
  process.exit(1);
}


function printTrackerHelp() {
  console.log(`shipctl tracker — workspace-scoped ticket reads + writes

SUBCOMMANDS
  shipctl tracker create-ticket --project-id <id> --title <s>
                                --body <md> [--labels a,b,c]
                                [--priority 0..4] [--body-file <path|->]
                                [--workspace <id>] [--json]

  shipctl tracker list-project-tickets --project-id <id>
                                       [--open-only true|false]
                                       [--limit N]
                                       [--workspace <id>] [--json]

PRIORITY (Linear convention)
  0  No priority   1  Urgent   2  High   3  Medium   4  Low

ENV
  SHIP_API_TOKEN             Required. Bearer PAT minted at /settings.
  SHIP_WORKSPACE_ID          Optional. Skips the /v1/workspaces lookup.
  SHIP_WORKSPACE_API_BASE    Optional override for the control plane.

EXIT
  0  ok
  1  arg / config / 4xx
  2  auth (401)
  3  network / 5xx
`);
}


async function trackerCreateTicketCommand(ctx, args) {
  const opts = parseCreateArgs(args);
  const body = (await readBodyFromOpts(opts)).trim();
  if (!body) {
    console.error("Pass --body <markdown> or --body-file <path|->");
    process.exit(1);
  }
  const { baseUrl, token, workspaceId } = await resolveContext(opts, ctx);
  const labels = opts.labels
    ? opts.labels
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : [];
  const result = await apiRequest(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker/tickets`,
    "POST",
    token,
    {
      project_id: opts.projectId,
      title: opts.title,
      body,
      labels,
      priority: opts.priority != null ? Number(opts.priority) : null,
    },
  );

  if (ctx?.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  console.log(`${result?.ticket_ref ?? "?"}\t${result?.url ?? ""}`);
}


function parseCreateArgs(args) {
  const out = {
    projectId: null,
    title: null,
    body: null,
    bodyFile: null,
    labels: null,
    priority: null,
    workspace: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  while (copy.length) {
    if (
      consumeStringFlag(copy, "--project-id", out, "projectId") ||
      consumeStringFlag(copy, "--title", out, "title") ||
      consumeStringFlag(copy, "--labels", out, "labels") ||
      consumeStringFlag(copy, "--priority", out, "priority") ||
      consumeStringFlag(copy, "--workspace", out, "workspace") ||
      consumeStringFlag(copy, "--base-url", out, "baseUrl") ||
      consumeBodyFlags(copy, out)
    ) {
      continue;
    }
    if (copy[0] === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (copy[0] === "--help" || copy[0] === "-h") {
      printTrackerHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.projectId) {
    console.error("--project-id is required");
    process.exit(1);
  }
  if (!out.title || !out.title.trim()) {
    console.error("--title is required");
    process.exit(1);
  }
  if (out.priority != null) {
    const n = Number(out.priority);
    if (!Number.isFinite(n) || n < 0 || n > 4) {
      console.error("--priority must be 0..4 (Linear: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low)");
      process.exit(1);
    }
  }
  return out;
}


async function trackerListProjectTicketsCommand(ctx, args) {
  const opts = parseListArgs(args);
  const { baseUrl, token, workspaceId } = await resolveContext(opts, ctx);
  const qs = new URLSearchParams();
  qs.set("open_only", opts.openOnly === false ? "false" : "true");
  if (opts.limit != null) qs.set("limit", String(opts.limit));
  const result = await apiRequest(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/tracker/projects/${encodeURIComponent(opts.projectId)}/tickets?${qs.toString()}`,
    "GET",
    token,
    null,
  );
  if (ctx?.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  for (const t of result?.tickets ?? []) {
    const labels = (t.labels || []).join(",") || "-";
    console.log(`${t.ticket_ref}\t${t.state ?? "-"}\t${labels}\t${t.title}`);
  }
}


function parseListArgs(args) {
  const out = {
    projectId: null,
    openOnly: true,
    limit: null,
    workspace: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  while (copy.length) {
    if (
      consumeStringFlag(copy, "--project-id", out, "projectId") ||
      consumeStringFlag(copy, "--workspace", out, "workspace") ||
      consumeStringFlag(copy, "--base-url", out, "baseUrl")
    ) {
      continue;
    }
    if (copy[0] === "--open-only" && copy[1] !== undefined) {
      const v = String(copy[1]).toLowerCase();
      out.openOnly = v !== "false" && v !== "0";
      copy.shift();
      copy.shift();
      continue;
    }
    if (copy[0] === "--limit" && copy[1] !== undefined) {
      out.limit = Math.max(1, Math.min(250, Number(copy[1]) || 100));
      copy.shift();
      copy.shift();
      continue;
    }
    if (copy[0] === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (copy[0] === "--help" || copy[0] === "-h") {
      printTrackerHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.projectId) {
    console.error("--project-id is required");
    process.exit(1);
  }
  return out;
}
