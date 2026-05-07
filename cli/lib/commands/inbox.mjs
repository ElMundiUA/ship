/**
 * `shipctl inbox` — write into the operator's mailbox from a routine.
 *
 * Routines like daily-retro / learning-capture / process-reviewer file
 * a single ``type=report`` letter per run; the operator opens it in the
 * mailbox preview and hits Acknowledge. SDLC pipelines occasionally
 * file ``approval`` / ``improvement`` items.
 *
 * Usage:
 *
 *   shipctl inbox create
 *     --type <report|improvement|approval|exception>
 *     --title "Daily retro 2026-05-07"
 *     --body  "<markdown>" | --body-file path/to/digest.md | --body-file -
 *     [--summary "<short list-row blurb>"]
 *     [--workspace <id>]
 *     [--ticket-ref <id>]
 *     [--json]
 *
 * Auth: bearer ``SHIP_API_TOKEN`` (mint at /settings).
 *
 * Exit codes:
 *   0  inbox item created
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


const VALID_TYPES = new Set([
  "report",
  "improvement",
  "approval",
  "exception",
]);


export async function inboxCommand(ctx, rest) {
  const [sub, ...args] = rest;
  if (!sub || sub === "help" || sub === "-h" || sub === "--help") {
    printInboxHelp();
    return;
  }
  if (sub === "create") {
    await inboxCreateCommand(ctx, args);
    return;
  }
  console.error(
    `Unknown 'shipctl inbox' subcommand: ${sub}\nRun: shipctl inbox --help`,
  );
  process.exit(1);
}


function printInboxHelp() {
  console.log(`shipctl inbox — file letters in the operator's mailbox

SUBCOMMANDS
  shipctl inbox create --type <kind> --title <s> --body <md>
                       [--summary <s>] [--ticket-ref <id>]
                       [--body-file <path|->] [--workspace <id>] [--json]

TYPES
  report        Read-only digest (daily retro, learning capture,
                process review). Operator hits Acknowledge.
  improvement   Actionable suggestion. Operator accepts / dismisses.
  approval      Gated decision. Operator approves / rejects / dismisses.
  exception     Policy edge case. Operator acknowledges / dismisses.

ENV
  SHIP_API_TOKEN             Required. Bearer PAT minted at /settings.
  SHIP_WORKSPACE_ID          Optional. Skips the /v1/workspaces lookup.
  SHIP_WORKSPACE_API_BASE    Optional override for the control plane.

EXIT
  0  letter filed
  1  arg / config / 4xx
  2  auth (401)
  3  network / 5xx
`);
}


async function inboxCreateCommand(ctx, args) {
  const opts = parseCreateArgs(args);
  const body = (await readBodyFromOpts(opts)).trim();
  if (!body) {
    console.error("Pass --body <markdown> or --body-file <path|->");
    process.exit(1);
  }
  const { baseUrl, token, workspaceId } = await resolveContext(opts, ctx);

  const result = await apiRequest(
    baseUrl,
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/inbox/items`,
    "POST",
    token,
    {
      type: opts.type,
      title: opts.title,
      body,
      summary: opts.summary || null,
      ticket_ref: opts.ticketRef || null,
    },
  );

  if (ctx?.json || opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  console.log(`Letter filed (${opts.type}): ${opts.title}`);
}


function parseCreateArgs(args) {
  const out = {
    type: null,
    title: null,
    body: null,
    bodyFile: null,
    summary: null,
    ticketRef: null,
    workspace: null,
    baseUrl: null,
    json: false,
  };
  const copy = [...args];
  while (copy.length) {
    if (
      consumeStringFlag(copy, "--type", out, "type") ||
      consumeStringFlag(copy, "--title", out, "title") ||
      consumeStringFlag(copy, "--summary", out, "summary") ||
      consumeStringFlag(copy, "--ticket-ref", out, "ticketRef") ||
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
      printInboxHelp();
      process.exit(0);
    }
    console.error(`Unknown flag: ${copy[0]}`);
    process.exit(1);
  }
  if (!out.type || !VALID_TYPES.has(out.type)) {
    console.error(
      `--type must be one of ${[...VALID_TYPES].join(" | ")}`,
    );
    process.exit(1);
  }
  if (!out.title || !out.title.trim()) {
    console.error("--title is required");
    process.exit(1);
  }
  return out;
}
