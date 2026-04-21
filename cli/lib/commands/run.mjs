/**
 * `shipctl run` — single entry-point for executing a Ship lane (RFC-0007).
 *
 * Today's scope (Phase 1):
 *   - `kind=once` lanes run end-to-end: resolve config, fetch pattern,
 *     check idempotency, emit the prompt to stdout, write the marker.
 *   - `kind=event` and `kind=schedule` lanes are recognised but emit a
 *     "not yet wired" exit-0 no-op. Phase 3 wires the reusable workflow
 *     that makes those kinds execute.
 *
 * The command intentionally does not fork an agent subprocess. The
 * reusable workflow pipes shipctl's stdout into the customer's agent
 * (Cursor Cloud, Claude Code, Codex, …) the same way `shipctl kickoff`
 * does today. That keeps the CLI agnostic about which agent runtime is
 * in use.
 *
 * Callback behaviour: if a callback URL is available via flags or env,
 * `shipctl run` reports `status=ok` on success and `status=fail` on any
 * failure path. Callback errors do not override the primary exit code
 * (a successful lane with a flaky callback still exits 0, but prints a
 * warning to stderr).
 */

import path from "node:path";

import { readConfig, findShipRoot } from "../config/io.mjs";
import { validateConfig, CONFIG_SCHEMA_VERSION } from "../config/schema.mjs";
import { fetchArtifact } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { readArtifactFile } from "../artifacts/fs-index.mjs";
import { decideRun, readMarker, writeMarker, sha256 } from "../state/idempotency.mjs";

const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_V1_CONFIG = 2;
const EXIT_CALLBACK = 3;
const EXIT_IDEMPOTENCY = 4;

const VALID_TRIGGERS = new Set(["event", "schedule", "manual", "once"]);

function printHelp() {
  console.log(`shipctl run — execute a Ship lane end-to-end.

USAGE
  shipctl run --lane <id> [--trigger <event|schedule|manual|once>]
              [--dry-run] [--offline]
              [--ship-run-id <uuid>] [--ship-callback-url <url>] [--ship-run-token <jwt>]
              [--cwd <dir>] [--json]

FLAGS
  --lane <id>               Lane id declared in .ship/config.yml. Required.
  --trigger <kind>          Force the trigger context (event|schedule|manual|once).
                            If omitted, inferred from GITHUB_EVENT_NAME / SHIP_RUN_TRIGGER.
  --dry-run                 Print the plan without touching idempotency markers or callback.
  --offline                 Read patterns from .ship/patterns/ instead of the methodology API.
                            (Phase 4 — today this falls back to the local Ship monorepo copy.)
  --ship-run-id <uuid>      Pipeline run id. Falls back to SHIP_RUN_ID env.
  --ship-callback-url <url> Full callback URL. Falls back to SHIP_CALLBACK_URL env.
  --ship-run-token <jwt>    Short-lived bearer. Falls back to SHIP_RUN_TOKEN env.
  --cwd <dir>               Repo root. Default: search upward for .ship/config.yml.
  --json                    Emit a structured summary on stdout.
  --help                    Show this help.

EXIT
  0  lane executed or no-op
  1  usage / config error
  2  config is v1 — run 'shipctl migrate' first
  3  callback failed (lane itself may have succeeded)
  4  idempotency marker read/write failure
 10  missing SHIP_RUN_TOKEN when a callback URL is configured

EXAMPLE (CI step emitted by the reusable workflow)
  shipctl run --lane seed_knowledge_starters | feed-to-agent
`);
}

/**
 * @param {{json?: boolean, dryRun?: boolean, baseUrl?: string}} ctx
 * @param {string[]} rest
 */
export async function runCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }
  if (!args.lane) {
    die(EXIT_USAGE, "`--lane <id>` is required.\nRun: shipctl run --help");
  }

  const cwd = args.cwd || process.cwd();
  const root = findShipRoot(cwd);
  if (!root) {
    die(
      EXIT_USAGE,
      `.ship/config.yml not found (searched from ${path.resolve(cwd)} upward). Run 'shipctl init' first.`,
    );
  }

  let config;
  try {
    const read = readConfig(cwd);
    config = read.config;
  } catch (err) {
    die(EXIT_USAGE, err instanceof Error ? err.message : String(err));
  }

  if (config.version !== CONFIG_SCHEMA_VERSION) {
    die(
      EXIT_V1_CONFIG,
      `.ship/config.yml is at v${config.version}; shipctl run requires v${CONFIG_SCHEMA_VERSION}.\nRun 'shipctl migrate' to upgrade.`,
    );
  }

  const validation = validateConfig(config);
  if (!validation.ok) {
    const msg = [
      "config is invalid:",
      ...validation.errors.map((e) => `  - ${e}`),
    ].join("\n");
    die(EXIT_USAGE, msg);
  }

  const lane = config.lanes?.[args.lane];
  if (!lane) {
    const known = Object.keys(config.lanes || {}).sort();
    die(
      EXIT_USAGE,
      `unknown lane '${args.lane}'. Known lanes: ${known.length ? known.join(", ") : "(none)"}`,
    );
  }

  const effectiveTrigger = resolveTrigger(args.trigger, lane.kind);
  if (!effectiveTrigger.fits) {
    /* Not an error — scheduler fired us but the lane doesn't want this
     * trigger. Exit 0 so parallel lanes in the same workflow don't all
     * fail just because one didn't match. */
    const summary = {
      lane: args.lane,
      kind: lane.kind,
      trigger: effectiveTrigger.trigger,
      status: "noop",
      reason: `lane.kind=${lane.kind} does not accept trigger=${effectiveTrigger.trigger}`,
    };
    emitSummary(ctx, args, summary);
    process.exit(EXIT_OK);
  }

  /* Phase 1: only `once` executes fully today. The other kinds validate
   * and exit 0 with a clear reason so CI wrappers can safely wire them
   * now and we flip on behaviour in Phase 3 without re-release. */
  if (lane.kind !== "once") {
    const summary = {
      lane: args.lane,
      kind: lane.kind,
      trigger: effectiveTrigger.trigger,
      status: "noop",
      reason: `lane.kind=${lane.kind} is recognised but not yet wired in shipctl run (Phase 3).`,
    };
    emitSummary(ctx, args, summary);
    process.exit(EXIT_OK);
  }

  /* --- kind=once path ------------------------------------------------ */

  const patternId = String(lane.pattern);
  const patternFetch = await fetchPatternBody({
    patternId,
    patternVersion: lane.pattern_version || null,
    offline: args.offline,
    root,
    ctx,
    config,
  });
  if (!patternFetch.ok) {
    die(EXIT_USAGE, patternFetch.error);
  }

  const patternBody = patternFetch.body;
  const patternSha = sha256(patternBody);

  const idem = lane.idempotency;
  let marker = null;
  try {
    marker = readMarker(cwd, idem.key);
  } catch (err) {
    await tryCallback(args, "fail", `idempotency read failed: ${err.message}`);
    die(EXIT_IDEMPOTENCY, err instanceof Error ? err.message : String(err));
  }

  const decision = decideRun(marker, patternBody, idem.reset_on || "version-change");
  if (!decision.run) {
    const summary = {
      lane: args.lane,
      kind: lane.kind,
      trigger: effectiveTrigger.trigger,
      status: "noop",
      reason: "already-done",
      marker: decision.marker,
    };
    await tryCallback(args, "ok", `lane ${args.lane}: already completed, no-op.`);
    emitSummary(ctx, args, summary);
    process.exit(EXIT_OK);
  }

  /* Dry-run stops here — no marker write, no callback, just print the
   * plan. We still emit the pattern body to stdout so operators can
   * eyeball what the agent would receive. */
  if (args.dryRun || ctx.dryRun) {
    const summary = {
      lane: args.lane,
      kind: lane.kind,
      trigger: effectiveTrigger.trigger,
      status: "dry-run",
      reason: decision.reason,
      pattern: { id: patternId, sha256: patternSha, source: patternFetch.source },
    };
    if (ctx.json || args.json) {
      console.log(JSON.stringify({ ...summary, pattern_body: patternBody }, null, 2));
    } else {
      console.error(`# ship: lane=${args.lane} kind=${lane.kind} trigger=${effectiveTrigger.trigger} (dry-run)`);
      process.stdout.write(patternBody.endsWith("\n") ? patternBody : `${patternBody}\n`);
    }
    process.exit(EXIT_OK);
  }

  /* Emit the prompt for the agent to consume (same contract as
   * `shipctl kickoff`). The reusable workflow pipes this into the
   * configured agent runtime. */
  if (!(ctx.json || args.json)) {
    const provider = resolveAgentProvider(config, args.lane);
    if (provider) console.error(`# ship: lane=${args.lane} agent.provider=${provider}`);
    process.stdout.write(patternBody.endsWith("\n") ? patternBody : `${patternBody}\n`);
  }

  try {
    writeMarker(cwd, idem.key, {
      lane: args.lane,
      pattern_id: patternId,
      pattern_sha256: patternSha,
      pattern_version: lane.pattern_version || null,
    });
  } catch (err) {
    await tryCallback(args, "fail", `idempotency write failed: ${err.message}`);
    die(EXIT_IDEMPOTENCY, err instanceof Error ? err.message : String(err));
  }

  const callbackResult = await tryCallback(
    args,
    "ok",
    `lane ${args.lane} completed (pattern ${patternId}@${patternSha.slice(0, 8)}).`,
  );

  if (ctx.json || args.json) {
    console.log(
      JSON.stringify(
        {
          lane: args.lane,
          kind: lane.kind,
          trigger: effectiveTrigger.trigger,
          status: "completed",
          pattern: { id: patternId, sha256: patternSha, source: patternFetch.source },
          callback: callbackResult,
        },
        null,
        2,
      ),
    );
  }

  process.exit(callbackResult.ok === false ? EXIT_CALLBACK : EXIT_OK);
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function parseArgs(rest) {
  const out = {
    lane: null,
    trigger: null,
    dryRun: false,
    offline: false,
    runId: null,
    callbackUrl: null,
    runToken: null,
    cwd: null,
    json: false,
    help: false,
  };
  const copy = [...rest];
  const str = (flag, key) => {
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
    const a = copy[0];
    if (a === "--help" || a === "-h") {
      out.help = true;
      copy.shift();
      continue;
    }
    if (a === "--dry-run") {
      out.dryRun = true;
      copy.shift();
      continue;
    }
    if (a === "--offline") {
      out.offline = true;
      copy.shift();
      continue;
    }
    if (a === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (str("--lane", "lane")) continue;
    if (str("--trigger", "trigger")) continue;
    if (str("--ship-run-id", "runId")) continue;
    if (str("--ship-callback-url", "callbackUrl")) continue;
    if (str("--ship-run-token", "runToken")) continue;
    if (str("--cwd", "cwd")) {
      out.cwd = path.resolve(out.cwd);
      continue;
    }
    die(EXIT_USAGE, `unknown argument: ${a}\nRun: shipctl run --help`);
  }
  if (out.trigger && !VALID_TRIGGERS.has(out.trigger)) {
    die(
      EXIT_USAGE,
      `--trigger must be one of ${[...VALID_TRIGGERS].join("|")}; got ${out.trigger}`,
    );
  }
  return out;
}

function resolveTrigger(explicit, laneKind) {
  const raw =
    explicit ||
    (process.env.SHIP_RUN_TRIGGER && process.env.SHIP_RUN_TRIGGER.trim()) ||
    inferFromEnv();
  const trigger = raw || "manual";

  /* `once` lanes only run under `manual` or `once` triggers. Scheduler
   * or event triggers must not accidentally repeat seeding because the
   * cron happens to tick. */
  if (laneKind === "once") {
    return { fits: trigger === "manual" || trigger === "once", trigger };
  }
  if (laneKind === "schedule") {
    return { fits: trigger === "schedule" || trigger === "manual", trigger };
  }
  if (laneKind === "event") {
    return { fits: trigger === "event" || trigger === "manual", trigger };
  }
  return { fits: false, trigger };
}

function inferFromEnv() {
  if (process.env.GITHUB_EVENT_NAME === "schedule") return "schedule";
  if (process.env.GITHUB_EVENT_NAME === "workflow_dispatch") return "manual";
  if (process.env.GITHUB_EVENT_NAME) return "event";
  return null;
}

function resolveAgentProvider(config, laneId) {
  const override = config.agent?.overrides?.[laneId]?.provider;
  if (override) return override;
  return config.agent?.default?.provider || null;
}

async function fetchPatternBody({ patternId, patternVersion, offline, root, ctx, config }) {
  /* 1) Running inside the Ship monorepo — read from disk. */
  const shipRepo = resolveShipRepoRootForCatalog();
  if (shipRepo) {
    const file = readArtifactFile(shipRepo, "pattern", patternId);
    if (file) return { ok: true, body: file.content, source: "monorepo" };
  }

  /* 2) --offline: read from the customer's materialised copy.
   * Phase 4 will introduce a proper lock file; today we accept any
   * `.ship/patterns/<id>.md` the operator dropped alongside the config. */
  if (offline) {
    const local = path.join(root, ".ship", "patterns", `${patternId}.md`);
    try {
      const { readFileSync } = await import("node:fs");
      const body = readFileSync(local, "utf8");
      return { ok: true, body, source: "offline" };
    } catch (err) {
      return {
        ok: false,
        error: `--offline: ${local} not found (${err instanceof Error ? err.message : err}). Phase 4 will add 'shipctl sync --lock' to materialise patterns.`,
      };
    }
  }

  /* 3) Network: same resolver `shipctl kickoff` uses. */
  const base = resolveMethodologyBase(ctx, config);
  try {
    const { content } = await fetchArtifact(base, "pattern", patternId, patternVersion || undefined);
    return { ok: true, body: content, source: "http" };
  } catch (err) {
    return {
      ok: false,
      error: `failed to fetch pattern ${patternId}: ${err instanceof Error ? err.message : err}`,
    };
  }
}

function resolveMethodologyBase(ctx, config) {
  const fromFlag = ctx.baseUrl;
  const raw = config?.api?.base_url;
  if (typeof raw === "string" && raw.trim()) {
    const u = raw.replace(/\/$/, "");
    return u.includes("/api/methodology") ? u : `${u}/api/methodology`;
  }
  return fromFlag;
}

async function tryCallback(args, status, summary) {
  const url = args.callbackUrl || process.env.SHIP_CALLBACK_URL;
  if (!url) return { ok: null, skipped: "no-callback-url" };
  const token = args.runToken || process.env.SHIP_RUN_TOKEN;
  if (!token) {
    console.error(
      "warn: SHIP_RUN_TOKEN missing; skipping callback. (Set via --ship-run-token or env.)",
    );
    return { ok: false, skipped: "no-token" };
  }
  const body = { status: status === "ok" ? "succeeded" : status === "fail" ? "failed" : status };
  if (summary) body.summary = String(summary).slice(0, 1024);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error(`warn: callback returned HTTP ${res.status} ${res.statusText}\n${text}`);
      return { ok: false, status: res.status };
    }
    return { ok: true, status: res.status };
  } catch (err) {
    console.error(`warn: callback POST failed: ${err instanceof Error ? err.message : err}`);
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

function emitSummary(ctx, args, summary) {
  if (ctx.json || args.json) {
    console.log(JSON.stringify(summary, null, 2));
  } else {
    console.error(
      `# ship: lane=${summary.lane} status=${summary.status}${summary.reason ? ` reason="${summary.reason}"` : ""}`,
    );
  }
}

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}
