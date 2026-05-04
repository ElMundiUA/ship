/**
 * `shipctl preflight` — Phase 4 lifecycle gate.
 *
 * The trigger workflow runs this *before* `shipctl run` so a missing
 * secret or an unauthorised role denial surfaces as a structured
 * inbox-shaped result instead of a half-spawned agent that crashes
 * mid-prompt. Two outputs depending on the result:
 *
 *   ready=true  → exit 0, JSON body shows the resolved deny list
 *                  + the env contract that passed.
 *   ready=false → exit 0 but `ready: false` and a list of
 *                  ``missing_secrets`` and/or ``denied_role`` reasons.
 *                  The workflow's case statement uses this to skip
 *                  the run cleanly without consuming a Cursor seat.
 *
 * Phase 4 MVP scope (ship narrow, expand on real need):
 *   - Secrets check: SHIP_API_TOKEN, SHIP_WORKSPACE_ID,
 *     SHIP_API_BASE (or SHIP_WORKSPACE_API_BASE), and CURSOR_API_KEY
 *     when provider is cursor (the default).
 *   - Role denial surfacing: read the resolved role's `denied_tools`
 *     so the workflow can decide whether the role is even runnable
 *     in this environment (e.g. a future check that flags "reviewer
 *     needs gh auth, not git push" rejections at the runner side).
 *   - No tool detection (gh, jq, gitleaks, etc) — those are workflow
 *     concerns, and the runner itself complains loudly. We do NOT
 *     want preflight to grow into a general 'verify' clone.
 */

import path from "node:path";

import { findShipRoot, readConfig } from "../config/io.mjs";
import { resolveProvider } from "../agents/index.mjs";

const EXIT_OK = 0;
const EXIT_USAGE = 2;


export async function preflightCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }

  const cwd = args.cwd || process.cwd();
  const root = findShipRoot(cwd);
  // Without a Ship root we can still preflight the env — a missing
  // ``.ship/config.yml`` is itself a missing-tool reason.
  const config = root ? readConfig(cwd).config : null;

  const env = readEnv();
  const missingSecrets = [];
  if (!env.apiToken) missingSecrets.push("SHIP_API_TOKEN");
  if (!env.workspaceId) missingSecrets.push("SHIP_WORKSPACE_ID");
  if (!env.apiBase) missingSecrets.push("SHIP_API_BASE");

  // Provider-specific secrets only when we know which provider the
  // workflow will resolve to. ``args.routine`` / ``args.specialist``
  // is optional — when absent we report the workspace-default
  // provider (typically ``cursor``).
  const provider = config
    ? resolveProvider(config, args.routine || args.specialist)
    : "cursor";
  if (provider === "cursor" && !env.cursorKey) {
    missingSecrets.push("CURSOR_API_KEY");
  }

  // Role-side denial surfacing. We don't *enforce* the deny list
  // here — that's the runner's job once an agent runtime ships
  // tool-execution metadata Ship can intercept. Preflight just
  // reports the list so the workflow can pre-empt unsupported
  // combinations (and so `verify` can audit drift between the
  // Ship default and the workspace override).
  let deniedTools = [];
  if (env.apiToken && env.apiBase && env.workspaceId && (args.routine || args.specialist)) {
    const slug = args.specialist || resolveSpecialistFromRoutine(config, args.routine);
    if (slug) {
      try {
        const role = await fetchResolvedRole({
          apiBase: env.apiBase,
          apiToken: env.apiToken,
          workspaceId: env.workspaceId,
          slug,
        });
        deniedTools = Array.isArray(role?.denied_tools) ? role.denied_tools : [];
      } catch (err) {
        // Role-resolve failures degrade — preflight stays useful for
        // the secret-check half even when the API is unreachable.
        if (!ctx.json && !args.json) {
          console.error(
            `warn: agent-role resolve failed (${err instanceof Error ? err.message : err}); deny-list unverified.`,
          );
        }
      }
    }
  }

  const ready = missingSecrets.length === 0;
  const result = {
    ready,
    provider,
    missing_secrets: missingSecrets,
    denied_tools: deniedTools,
    routine: args.routine || null,
    specialist: args.specialist || null,
  };

  if (ctx.json || args.json) {
    console.log(JSON.stringify(result, null, 2));
    process.exit(EXIT_OK);
  }

  if (!ready) {
    console.error(`Ship preflight: NOT READY — missing ${missingSecrets.join(", ")}`);
  } else {
    console.log(`Ship preflight: ready (${provider}${deniedTools.length ? `, deny ${deniedTools.length}` : ""})`);
  }
  process.exit(EXIT_OK);
}


function parseArgs(rest) {
  const out = {
    routine: null,
    specialist: null,
    cwd: null,
    json: false,
    help: false,
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") { out.help = true; copy.shift(); continue; }
    if (a === "--json") { out.json = true; copy.shift(); continue; }
    if (a === "--routine" && copy[1] !== undefined) { out.routine = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--specialist" && copy[1] !== undefined) { out.specialist = copy[1]; copy.splice(0, 2); continue; }
    if (a === "--cwd" && copy[1] !== undefined) { out.cwd = path.resolve(copy[1]); copy.splice(0, 2); continue; }
    console.error(`unknown argument: ${a}`);
    process.exit(EXIT_USAGE);
  }
  return out;
}


function printHelp() {
  console.log(`shipctl preflight — verify the env + role contract before launching the runner.

USAGE
  shipctl preflight [--routine <id> | --specialist <slug>] [--json] [--cwd <dir>]

OUTPUT
  JSON shape:
    {
      "ready": true|false,
      "provider": "cursor"|...,
      "missing_secrets": ["SHIP_API_TOKEN", ...],
      "denied_tools": ["git_commit", ...],   // resolved role's deny list
      "routine": "...",
      "specialist": "..."
    }

EXIT
  0 always (so the workflow's case statement can branch on the JSON);
  the workflow checks 'ready' to decide whether to skip the run.
`);
}


function readEnv() {
  return {
    apiBase: stripSlash(
      process.env.SHIP_API_BASE || process.env.SHIP_WORKSPACE_API_BASE || "",
    ),
    apiToken: process.env.SHIP_API_TOKEN || "",
    workspaceId: process.env.SHIP_WORKSPACE_ID || "",
    cursorKey: process.env.CURSOR_API_KEY || "",
  };
}


function stripSlash(s) {
  return s.replace(/\/+$/, "");
}


function resolveSpecialistFromRoutine(config, routineId) {
  if (!routineId || !config) return null;
  const routine = config?.process?.routines?.[routineId] || config?.routines?.[routineId];
  if (!routine || typeof routine !== "object") return null;
  const direct = typeof routine.specialist === "string" ? routine.specialist : null;
  if (direct) return direct;
  const nested = typeof routine.specialist?.id === "string" ? routine.specialist.id : null;
  if (nested) return nested;
  // Legacy ``pattern: role-X`` carries the slug as ``X``.
  const pattern = typeof routine.pattern === "string" ? routine.pattern : null;
  if (pattern && pattern.startsWith("role-")) return pattern.slice("role-".length);
  return null;
}


async function fetchResolvedRole({ apiBase, apiToken, workspaceId, slug }) {
  const url = `${apiBase}/v1/workspaces/${encodeURIComponent(workspaceId)}/agent-roles/${encodeURIComponent(slug)}/resolve`;
  const res = await fetch(url, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${apiToken}`,
    },
  });
  if (!res.ok) {
    throw new Error(`agent-roles resolve ${res.status}`);
  }
  return res.json();
}
