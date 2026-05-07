/**
 * `shipctl project` — manage tracker projects from a routine.
 *
 * Today the only subcommand is ``find-or-create``: the reviewer
 * routines (tech-reviewer, qa-reviewer, security-officer) call this
 * once per run to resolve their dedicated holding-pen project ("Tech
 * Debt", "QA Debt", "Security") on the workspace's bound tracker.
 * First run creates; every subsequent run short-circuits on the
 * case-insensitive name match — no list-then-create race.
 *
 * Usage:
 *
 *   shipctl project find-or-create
 *     --name "Tech Debt"
 *     --body "<markdown body for the new project — only used on create>"
 *     [--description "<one-liner; ≤240 chars; only used on create>"]
 *     [--workspace <id>]
 *     [--json]
 *
 * Output (default): ``project_id\tname\tcreated`` on a single line so
 * shell scripts can parse it. ``--json`` returns the full server
 * response.
 *
 * Auth: bearer ``SHIP_API_TOKEN``.
 *
 * Exit codes:
 *   0  project resolved (found or created)
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


export async function projectCommand(ctx, rest) {
  const [sub, ...args] = rest;
  if (!sub || sub === "help" || sub === "-h" || sub === "--help") {
    printProjectHelp();
    return;
  }
  if (sub === "find-or-create") {
    await projectFindOrCreateCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl project' subcommand: ${sub}\nRun: shipctl project --help`,
  );
  process.exit(1);
}


function printProjectHelp() {
  console.log(`shipctl project — manage tracker projects

SUBCOMMANDS
  shipctl project find-or-create --name <s> --body <md>
                                 [--description <s>] [--body-file <path|->]
                                 [--workspace <id>] [--json]

ENV
  SHIP_API_TOKEN             Required. Bearer PAT minted at /settings.
  SHIP_WORKSPACE_ID          Optional. Skips the /v1/workspaces lookup.
  SHIP_WORKSPACE_API_BASE    Optional override for the control plane.

EXIT
  0  project resolved
  1  arg / config / 4xx
  2  auth (401)
  3  network / 5xx
`);
}


async function projectFindOrCreateCommand(ctx, args) {
  const opts = parseFindOrCreateArgs(args);
  const body = (await readBodyFromOpts(opts)).trim();
  if (!body) {
    console.error("Pass --body <markdown> or --body-file <path|->");
    process.exit(1);
  }
  const { baseUrl, token, workspaceId } = await resolveContext(opts, ctx);

  const result = await apiRequest(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/projects/find-or-create`,
    "POST",
    token,
    {
      name: opts.name,
      body,
      description: opts.description || null,
    },
  );

  if (ctx?.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  const project = result?.project ?? {};
  const created = result?.created ? "created" : "existing";
  console.log(
    `${project.id ?? ""}\t${project.name ?? opts.name}\t${created}`,
  );
}


function parseFindOrCreateArgs(args) {
  const out = {
    name: null,
    body: null,
    bodyFile: null,
    description: null,
    workspace: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  while (copy.length) {
    if (
      consumeStringFlag(copy, "--name", out, "name") ||
      consumeStringFlag(copy, "--description", out, "description") ||
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
      printProjectHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.name || !out.name.trim()) {
    console.error("--name is required");
    process.exit(1);
  }
  return out;
}
