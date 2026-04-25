import fs from "node:fs";
import path from "node:path";

import { readConfig } from "../config/io.mjs";
import { CONFIG_SCHEMA_VERSION, validateConfig } from "../config/schema.mjs";
import { apiGet } from "../http.mjs";
import {
  buildSpecialistPromptBundle,
  renderSpecialistPromptBundleMarkdown,
} from "../process/specialist-prompt-contract.mjs";

export async function processCommand(ctx, rest) {
  const [sub, ...args] = rest;
  if (!sub || sub === "help" || sub === "-h" || sub === "--help") {
    printProcessHelp();
    return;
  }
  if (sub === "prompt" || sub === "bundle") {
    await processPromptCommand(ctx, args);
    return;
  }
  if (sub === "tickets") {
    await processTicketsCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl process' subcommand: ${sub}\nRun: shipctl process --help`,
  );
  process.exit(1);
}

function printProcessHelp() {
  console.log(`shipctl process — assemble Process/FSM specialist context

USAGE
  shipctl process prompt --state <state-id> [--ticket-json <json>] [--ticket-file <path>]
                         [--ticket-id <id>] [--ticket-title <title>] [--ticket-url <url>]
                         [--ticket-description <text>] [--policies-file <path>]
                         [--cwd <dir>] [--json]
  shipctl process prompt --specialist <specialist-id> [...]
  shipctl process tickets --workspace <id> [--process <id>] [--tracker <kind>]
                          [--query <text>] [--state open|closed|all] [--limit <n>]
                          [--project-hint <hint>] [--assignee-me] [--assignee <user>]
                          [--json]

WHAT IT EMITS
  A specialist prompt bundle assembled from .ship/config.yml:
    - process and state identity
    - specialist template fields from the selected state
    - ticket context supplied by the caller / tracker picker
    - allowed outgoing FSM transitions from process.transitions
    - workspace policies supplied by --policies-file
    - mandatory knowledge-first and Ship-boundary guardrails

The command does not mutate tracker tickets, execute transitions, or write to
the repository. It prepares context for an agent runtime; Ship remains
responsible for side effects, FSM validation, audit logging, and PR-only writes.`);
}

async function processTicketsCommand(ctx, args) {
  const opts = parseTicketsArgs(args);
  const baseUrl = resolveWorkspaceBaseUrl(opts.baseUrl || ctx.baseUrl);
  const token = process.env.SHIP_API_TOKEN || "";
  if (!token) {
    console.error("SHIP_API_TOKEN is required for shipctl process tickets.");
    process.exit(1);
  }
  const processId = opts.processId || "development";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({
    tracker: opts.tracker,
    project_hint: opts.projectHint,
    state: opts.state,
    query: opts.query,
    assignee: opts.assignee,
    limit: opts.limit,
  })) {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  if (opts.assigneeMe) params.set("assignee_me", "true");
  const suffix = params.toString() ? `?${params}` : "";
  const data = await apiGet(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(opts.workspace)}/processes/${encodeURIComponent(processId)}/tickets${suffix}`,
  );
  if (ctx.json || opts.json) {
    console.log(JSON.stringify(data, null, 2));
    return;
  }
  const tickets = Array.isArray(data?.tickets) ? data.tickets : [];
  console.log(`Tracker: ${data?.tracker || opts.tracker || "(auto)"}`);
  if (!tickets.length) {
    console.log("No tickets matched.");
    return;
  }
  for (const ticket of tickets) {
    const id = ticket.key || ticket.display_id || ticket.id || "(no id)";
    const title = ticket.title || "(untitled)";
    const status = ticket.status ? ` [${ticket.status}]` : "";
    const url = ticket.url ? `\n  ${ticket.url}` : "";
    console.log(`- ${id}${status}: ${title}${url}`);
  }
}

async function processPromptCommand(ctx, args) {
  const opts = parsePromptArgs(args);
  const cwd = opts.cwd || process.cwd();
  let config;
  try {
    config = readConfig(cwd).config;
  } catch (err) {
    die(err instanceof Error ? err.message : String(err));
  }
  if (config.version !== CONFIG_SCHEMA_VERSION) {
    die(
      `.ship/config.yml is at v${config.version}; shipctl process prompt requires v${CONFIG_SCHEMA_VERSION}.\nRun 'shipctl migrate' to upgrade.`,
    );
  }
  const validation = validateConfig(config);
  if (!validation.ok) {
    die(["config is invalid:", ...validation.errors.map((e) => `  - ${e}`)].join("\n"));
  }
  const processConfig = config.process;
  if (!processConfig || typeof processConfig !== "object") {
    die("process: missing from .ship/config.yml");
  }

  const state = selectState(processConfig, opts);
  const allowedTransitions = Array.isArray(processConfig.transitions)
    ? processConfig.transitions.filter((transition) => transition.from === state.id)
    : [];
  const bundle = buildSpecialistPromptBundle({
    process: processConfig,
    state,
    allowedTransitions,
    ticket: resolveTicket(opts),
    policies: readOptionalFile(opts.policiesFile, "policies"),
  });

  if (ctx.json || opts.json) {
    console.log(JSON.stringify(bundle, null, 2));
    return;
  }
  process.stdout.write(renderSpecialistPromptBundleMarkdown(bundle));
}

function parsePromptArgs(args) {
  const out = {
    state: null,
    specialist: null,
    cwd: null,
    json: false,
    ticketJson: null,
    ticketFile: null,
    ticket: {},
    policiesFile: null,
  };
  const copy = [...args];
  const readValue = (flag) => {
    const current = copy.shift();
    if (current === flag) {
      if (copy.length === 0) die(`${flag} requires a value`);
      return String(copy.shift());
    }
    const prefix = `${flag}=`;
    if (current && current.startsWith(prefix)) {
      return current.slice(prefix.length);
    }
    copy.unshift(current);
    return null;
  };

  while (copy.length) {
    const arg = copy[0];
    if (arg === "--help" || arg === "-h") {
      printProcessHelp();
      process.exit(0);
    }
    if (arg === "--json") {
      copy.shift();
      out.json = true;
      continue;
    }
    const state = readValue("--state");
    if (state !== null) {
      out.state = state;
      continue;
    }
    const specialist = readValue("--specialist");
    if (specialist !== null) {
      out.specialist = specialist;
      continue;
    }
    const cwd = readValue("--cwd");
    if (cwd !== null) {
      out.cwd = path.resolve(cwd);
      continue;
    }
    const ticketJson = readValue("--ticket-json");
    if (ticketJson !== null) {
      out.ticketJson = ticketJson;
      continue;
    }
    const ticketFile = readValue("--ticket-file");
    if (ticketFile !== null) {
      out.ticketFile = path.resolve(ticketFile);
      continue;
    }
    const policiesFile = readValue("--policies-file");
    if (policiesFile !== null) {
      out.policiesFile = path.resolve(policiesFile);
      continue;
    }
    for (const [flag, key] of [
      ["--ticket-id", "id"],
      ["--ticket-key", "key"],
      ["--ticket-title", "title"],
      ["--ticket-url", "url"],
      ["--ticket-status", "status"],
      ["--ticket-description", "description"],
    ]) {
      const value = readValue(flag);
      if (value !== null) {
        out.ticket[key] = value;
        break;
      }
    }
    if (copy[0] === arg) {
      die(`unknown argument: ${arg}\nRun: shipctl process prompt --help`);
    }
  }
  if (!out.state && !out.specialist) {
    die("either --state <state-id> or --specialist <specialist-id> is required");
  }
  if (out.state && out.specialist) {
    die("use either --state or --specialist, not both");
  }
  return out;
}

function parseTicketsArgs(args) {
  const out = {
    workspace: null,
    processId: "development",
    tracker: null,
    projectHint: null,
    query: null,
    state: "open",
    limit: 10,
    assigneeMe: false,
    assignee: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  const readValue = (flag) => {
    const current = copy.shift();
    if (current === flag) {
      if (copy.length === 0) die(`${flag} requires a value`);
      return String(copy.shift());
    }
    const prefix = `${flag}=`;
    if (current && current.startsWith(prefix)) {
      return current.slice(prefix.length);
    }
    copy.unshift(current);
    return null;
  };
  while (copy.length) {
    const arg = copy[0];
    if (arg === "--help" || arg === "-h") {
      printProcessHelp();
      process.exit(0);
    }
    if (arg === "--json") {
      copy.shift();
      out.json = true;
      continue;
    }
    if (arg === "--assignee-me") {
      copy.shift();
      out.assigneeMe = true;
      continue;
    }
    for (const [flag, key] of [
      ["--workspace", "workspace"],
      ["--process", "processId"],
      ["--tracker", "tracker"],
      ["--project-hint", "projectHint"],
      ["--query", "query"],
      ["--state", "state"],
      ["--limit", "limit"],
      ["--assignee", "assignee"],
      ["--base-url", "baseUrl"],
    ]) {
      const value = readValue(flag);
      if (value !== null) {
        out[key] = key === "limit" ? Number(value) : value;
        break;
      }
    }
    if (copy[0] === arg) {
      die(`unknown argument: ${arg}\nRun: shipctl process tickets --help`);
    }
  }
  if (!out.workspace) die("--workspace <id> is required");
  if (!["open", "closed", "all"].includes(out.state)) {
    die("--state must be one of open|closed|all");
  }
  if (!Number.isInteger(out.limit) || out.limit < 1 || out.limit > 25) {
    die("--limit must be an integer between 1 and 25");
  }
  return out;
}

function selectState(processConfig, opts) {
  const states = Array.isArray(processConfig.states) ? processConfig.states : [];
  if (opts.state) {
    const state = states.find((item) => item.id === opts.state);
    if (!state) die(`unknown process state ${JSON.stringify(opts.state)}`);
    return state;
  }
  const state = states.find((item) => {
    const specialist = item.specialist;
    if (typeof specialist === "string") return specialist === opts.specialist;
    if (specialist && typeof specialist === "object") {
      return specialist.id === opts.specialist || specialist.name === opts.specialist;
    }
    return false;
  });
  if (!state) die(`unknown process specialist ${JSON.stringify(opts.specialist)}`);
  return state;
}

function resolveTicket(opts) {
  const parts = [];
  if (opts.ticketFile) {
    const body = readOptionalFile(opts.ticketFile, "ticket");
    if (body) {
      parts.push(parseTicketJson(body, `ticket file ${opts.ticketFile}`));
    }
  }
  if (opts.ticketJson) {
    parts.push(parseTicketJson(opts.ticketJson, "--ticket-json"));
  }
  if (Object.keys(opts.ticket).length) {
    parts.push(opts.ticket);
  }
  if (!parts.length) return null;
  return Object.assign({}, ...parts);
}

function parseTicketJson(value, label) {
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      die(`${label}: must be a JSON object`);
    }
    return parsed;
  } catch (err) {
    die(`${label}: failed to parse JSON (${err instanceof Error ? err.message : err})`);
  }
}

function readOptionalFile(filePath, label) {
  if (!filePath) return null;
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch (err) {
    die(`failed to read ${label} file ${filePath}: ${err instanceof Error ? err.message : err}`);
  }
}

function resolveWorkspaceBaseUrl(explicit) {
  return (
    explicit ||
    process.env.SHIP_WORKSPACE_API_BASE ||
    process.env.SHIP_API_BASE ||
    "https://api.ship.elmundi.com"
  );
}

function die(message) {
  console.error(message);
  process.exit(1);
}
