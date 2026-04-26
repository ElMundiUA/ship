/**
 * `shipctl run` — single entry-point for executing a Ship routine.
 *
 * Today's scope (Phase 1):
 *   - `kind=once` routines run with local idempotency markers.
 *   - `kind=event` and `kind=schedule` routines execute when `shipctl trigger`
 *     says they are due; Ship only claims the schedule window.
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
 * (a successful routine with a flaky callback still exits 0, but prints a
 * warning to stderr).
 */

import fs from "node:fs";
import path from "node:path";

import { readConfig, findShipRoot } from "../config/io.mjs";
import {
  validateConfig,
  CONFIG_SCHEMA_VERSION,
  LANE_FANOUT_MODES,
} from "../config/schema.mjs";
import {
  executableFanout,
  executableIds,
  executablePatterns,
  resolveExecutable,
} from "../runtime/routines.mjs";
import { fetchArtifact } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { readArtifactFile } from "../artifacts/fs-index.mjs";
import { decideRun, readMarker, writeMarker, sha256 } from "../state/idempotency.mjs";
import { readLockfile, lookupLock, verifyBody } from "../state/lockfile.mjs";

const EXIT_OK = 0;
const EXIT_USAGE = 1;
const EXIT_V1_CONFIG = 2;
const EXIT_CALLBACK = 3;
const EXIT_IDEMPOTENCY = 4;

const VALID_TRIGGERS = new Set(["event", "schedule", "manual", "once"]);

function printHelp() {
  console.log(`shipctl run — execute a Ship routine.

WHAT THIS COMMAND IS FOR
  shipctl run is the **Run** dispatch entry point. It resolves a
  routine from .ship/config.yml, fetches its pattern body, checks
  idempotency, and emits the prompt for an agent to consume. Behaviour
  by routine trigger:
    - kind: once             — executed fully here, locally.
    - kind: lane / event /   — recognised but NOT executed locally;
        schedule               those run via the workspace's GitHub
                               Actions runner using the reusable
                               .github/workflows/run-agent.yml. shipctl
                               run exits 0 with a no-op summary so CI
                               wrappers can wire them safely.

USAGE
  shipctl run --routine <id> [--pattern <id>] [--fanout <matrix|sequential|concurrent>]
              [--trigger <event|schedule|manual|once>]
              [--dry-run] [--offline]
              [--ship-run-id <uuid>] [--ship-callback-url <url>] [--ship-run-token <jwt>]
              [--cwd <dir>] [--json]

FLAGS
  --routine <id>            Routine id declared in process.routines. Required.
  --lane <id>               Back-compat alias for --routine.
  --pattern <id>            For multi-pattern lanes: run only this pattern. This
                            is the per-entry call issued by the matrix workflow
                            (one matrix job per pattern). Must be one of the
                            lane's declared patterns.
  --fanout <mode>           Override the lane's configured fan-out for this run
                            (matrix|sequential|concurrent). Meaningful only
                            when the lane has ≥2 patterns and --pattern is not
                            set. Matrix mode without --pattern is rejected;
                            it requires a driving workflow.
  --trigger <kind>          Force the trigger context (event|schedule|manual|once).
                            If omitted, inferred from GITHUB_EVENT_NAME / SHIP_RUN_TRIGGER.
  --dry-run                 Print the plan without touching idempotency markers or callback.
  --offline                 Resolve patterns exclusively via .ship/shipctl.lock.json
                            and .ship/cache/ — never talks to the methodology API.
                            Fails if the lockfile or a cached body is missing.
                            Generate one with 'shipctl sync --lock'.
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
  shipctl run --routine daily_digest | feed-to-agent
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
  if (!args.routine) {
    die(EXIT_USAGE, "`--routine <id>` is required (legacy alias: `--lane <id>`).\nRun: shipctl run --help");
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

  const resolved = resolveExecutable(config, args.routine);
  if (!resolved) {
    const known = executableIds(config);
    const joined = [...known.routines, ...known.lanes].sort();
    die(
      EXIT_USAGE,
      `unknown lane/routine '${args.routine}'. Known routines: ${joined.length ? joined.join(", ") : "(none)"}`,
    );
  }
  const executable = resolved.executable;

  const effectiveTrigger = resolveTrigger(args.trigger, executable.kind);
  if (!effectiveTrigger.fits) {
    /* Not an error — scheduler fired us but the lane doesn't want this
     * trigger. Exit 0 so parallel lanes in the same workflow don't all
     * fail just because one didn't match. */
    const summary = {
      routine: args.routine,
      lane: resolved.kind === "lane" ? args.routine : undefined,
      kind: executable.kind,
      trigger: effectiveTrigger.trigger,
      status: "noop",
      reason: `routine.kind=${executable.kind} does not accept trigger=${effectiveTrigger.trigger}`,
    };
    emitSummary(ctx, args, summary);
    process.exit(EXIT_OK);
  }

  // RFC-0008 C3.1/C3.2: resolve the list of patterns that this invocation
  // should execute.
  //
  //   --pattern <id>     → run only that pattern (the per-entry call
  //                         issued by the matrix workflow). The pattern
  //                         must be one of the lane's declared patterns,
  //                         otherwise we refuse so typos don't silently
  //                         execute an unrelated pattern.
  //   (none)             → run every pattern the lane declares, using
  //                         the lane's fan-out mode. Matrix mode without
  //                         --pattern is rejected because it requires a
  //                         driving workflow (see run-agent.yml).
  const allPatterns = executablePatterns(executable);
  const promptBody = executable.prompt;
  if (allPatterns.length === 0 && !promptBody) {
    die(EXIT_USAGE, `routine ${JSON.stringify(args.routine)} declares no patterns or prompt.`);
  }

  const effectiveFanout = args.fanout || executableFanout(executable);
  let patternsToRun;
  let runMode; // ``single`` | ``sequential`` | ``concurrent``
  if (args.pattern) {
    if (!allPatterns.includes(args.pattern)) {
      die(
        EXIT_USAGE,
        `--pattern=${JSON.stringify(args.pattern)} is not declared on lane/routine ${JSON.stringify(args.routine)}. ` +
          `Known patterns: ${allPatterns.join(", ")}.`,
      );
    }
    patternsToRun = [args.pattern];
    runMode = "single";
  } else if (allPatterns.length === 0 && promptBody) {
    patternsToRun = [];
    runMode = "single";
  } else if (allPatterns.length === 1) {
    patternsToRun = allPatterns;
    runMode = "single";
  } else if (effectiveFanout === "matrix") {
    die(
      EXIT_USAGE,
      `routine ${JSON.stringify(args.routine)} has fanout=matrix and ${allPatterns.length} patterns ` +
        `but no --pattern was provided. Matrix mode dispatches one 'shipctl run --pattern <id>' per ` +
        `pattern via the workflow (see run-agent.yml). To run them in-process instead, pass ` +
        `--fanout sequential or --fanout concurrent.`,
    );
  } else {
    patternsToRun = allPatterns;
    runMode = effectiveFanout;
  }

  // Idempotency markers are lane-scoped (not per-pattern) so we read
  // once up front; per-pattern decisions are derived from the
  // concatenated pattern SHA set below so a change to any member of
  // the list re-triggers the run (expected behaviour for audit lanes).
  const idem = executable.kind === "once" ? executable.idempotency : null;
  let marker = null;
  if (idem) {
    try {
      marker = readMarker(cwd, idem.key);
    } catch (err) {
      await tryCallback(args, "fail", `idempotency read failed: ${err.message}`);
      die(EXIT_IDEMPOTENCY, err instanceof Error ? err.message : String(err));
    }
  }

  // Fetch every pattern body first so we can reject the whole run
  // atomically if any one is unavailable — partial success is worse
  // than a hard failure here (the caller can retry once the fetch
  // error is cleared).
  const fetchJobs = patternsToRun.map((patternId) =>
    fetchPatternBody({
      patternId,
      patternVersion: executable.pattern_version || null,
      offline: args.offline,
      root,
      ctx,
      config,
    }).then((result) => ({ patternId, result })),
  );
  // `sequential` vs `concurrent` only differ for future in-process agent
  // invocation; today's CLI just emits the pattern bodies to stdout, so
  // both modes fetch in parallel and print in declared order. We still
  // record the requested mode on the summary so downstream consumers
  // (and future work) can see the intent.
  const fetched = await Promise.all(fetchJobs);
  for (const { patternId, result } of fetched) {
    if (!result.ok) {
      die(EXIT_USAGE, `pattern ${patternId}: ${result.error}`);
    }
  }
  const runs = promptBody && patternsToRun.length === 0
    ? [{
        patternId: `${args.routine}:prompt`,
        body: promptBody,
        source: "routine",
        sha256: sha256(promptBody),
      }]
    : fetched.map(({ patternId, result }) => ({
    patternId,
    body: result.body,
    source: result.source,
    sha256: sha256(result.body),
  }));

  // Composite SHA over all pattern bodies. ``reset_on=version-change``
  // fires when any member's body drifts — which is the correct
  // semantics for a multi-pattern audit lane: if one role's playbook
  // updates, we want the whole lane to re-run.
  const compositeBody = runs.map((r) => `#${r.patternId}\n${r.body}`).join("\n---\n");
  const decision = idem
    ? decideRun(marker, compositeBody, idem.reset_on || "version-change")
    : { run: true, reason: "trigger-router-due", marker: null };
  if (!decision.run) {
    const summary = {
      routine: args.routine,
      lane: resolved.kind === "lane" ? args.routine : undefined,
      kind: executable.kind,
      trigger: effectiveTrigger.trigger,
      status: "noop",
      reason: "already-done",
      marker: decision.marker,
      patterns: runs.map((r) => ({ id: r.patternId, sha256: r.sha256 })),
    };
    await tryCallback(
      args,
      "ok",
      `routine ${args.routine}: already completed, no-op.`,
      runMode === "single"
        ? { pattern_id: runs[0].patternId, pattern_sha256: runs[0].sha256, noop: true }
        : { patterns: runs.map((r) => r.patternId), noop: true },
    );
    emitSummary(ctx, args, summary);
    process.exit(EXIT_OK);
  }

  /* Dry-run stops here — no marker write, no callback, just print the
   * plan. We still emit the pattern bodies to stdout so operators can
   * eyeball what the agent would receive. Multi-pattern runs print
   * each body preceded by a ``# ship: pattern=<id>`` banner so the
   * agent-side (or a human) can split them back apart. */
  if (args.dryRun || ctx.dryRun) {
    const summary = {
      routine: args.routine,
      lane: resolved.kind === "lane" ? args.routine : undefined,
      kind: executable.kind,
      trigger: effectiveTrigger.trigger,
      status: "dry-run",
      reason: decision.reason,
      mode: runMode,
      patterns: runs.map((r) => ({ id: r.patternId, sha256: r.sha256, source: r.source })),
    };
    if (runs.length === 1) {
      summary.pattern = { id: runs[0].patternId, sha256: runs[0].sha256, source: runs[0].source };
    }
    if (ctx.json || args.json) {
      console.log(
        JSON.stringify(
          { ...summary, pattern_bodies: Object.fromEntries(runs.map((r) => [r.patternId, r.body])) },
          null,
          2,
        ),
      );
    } else {
      console.error(
        `# ship: routine=${args.routine} kind=${executable.kind} trigger=${effectiveTrigger.trigger} mode=${runMode} (dry-run)`,
      );
      emitPatternBodies(runs, { json: false });
    }
    process.exit(EXIT_OK);
  }

  /* Emit the prompt(s) for the agent to consume (same contract as
   * `shipctl kickoff`). The reusable workflow pipes stdout into the
   * configured agent runtime; multi-pattern output is delimited by a
   * banner line per pattern so consumers can split on it.
   *
   * Workspace policy injection (RFC-Workspace-policy): before any
   * pattern body, fetch the workspace's prose-rule policies from the
   * backend and prepend them as a markdown block. This makes the
   * agent treat the policies as hard preamble — the same shape the
   * Navigator chat injects into ``TopicService.assemble_messages``.
   * Best-effort: a missing token, missing callback URL, or a network
   * failure quietly skips the prepend so local / offline runs still
   * work. */
  if (!(ctx.json || args.json)) {
    const provider = resolveAgentProvider(config, args.routine);
    if (provider) console.error(`# ship: routine=${args.routine} agent.provider=${provider} mode=${runMode}`);
    const preamble = await fetchPoliciesPreamble(args);
    if (preamble) emitPoliciesPreamble(preamble);
    emitPatternBodies(runs, { json: false });
  }

  if (idem) {
    try {
      writeMarker(cwd, idem.key, {
        routine: args.routine,
        lane: resolved.kind === "lane" ? args.routine : undefined,
        pattern_id: runs[0].patternId,
        pattern_sha256: sha256(compositeBody),
        pattern_version: executable.pattern_version || null,
        patterns: runs.map((r) => ({ id: r.patternId, sha256: r.sha256 })),
      });
    } catch (err) {
      await tryCallback(args, "fail", `idempotency write failed: ${err.message}`);
      die(EXIT_IDEMPOTENCY, err instanceof Error ? err.message : String(err));
    }
  }

  const callbackMetrics = runMode === "single"
    ? { pattern_id: runs[0].patternId, pattern_sha256: runs[0].sha256 }
    : {
        pattern_id: runs[0].patternId,
        pattern_sha256: runs[0].sha256,
        patterns: runs.map((r) => r.patternId).join(","),
      };
  const callbackSummary = runMode === "single"
    ? `routine ${args.routine} completed (pattern ${runs[0].patternId}@${runs[0].sha256.slice(0, 8)}).`
    : `routine ${args.routine} completed (${runs.length} patterns, mode=${runMode}).`;
  const callbackResult = await tryCallback(args, "ok", callbackSummary, callbackMetrics);

  if (ctx.json || args.json) {
    // For single-pattern runs we keep the legacy ``pattern: {…}`` key
    // alongside the new ``patterns: […]`` list so existing consumers
    // (and tests) don't break when they upgrade shipctl before
    // starting to declare multi-pattern lanes.
    const summaryPayload = {
      routine: args.routine,
      lane: resolved.kind === "lane" ? args.routine : undefined,
      kind: executable.kind,
      trigger: effectiveTrigger.trigger,
      status: "completed",
      mode: runMode,
      patterns: runs.map((r) => ({ id: r.patternId, sha256: r.sha256, source: r.source })),
      callback: callbackResult,
    };
    if (runs.length === 1) {
      summaryPayload.pattern = { id: runs[0].patternId, sha256: runs[0].sha256, source: runs[0].source };
    }
    console.log(JSON.stringify(summaryPayload, null, 2));
  }

  process.exit(callbackResult.ok === false ? EXIT_CALLBACK : EXIT_OK);
}

/**
 * Stream pattern bodies to stdout. For single-pattern runs we write
 * the body as-is (identical byte output to the pre-multi-pattern
 * behaviour, keeping the test harness stable). For multi-pattern
 * runs we precede each body with a ``# ship: pattern=<id>`` banner so
 * downstream consumers can re-split the stream.
 */
function emitPatternBodies(runs, _opts) {
  if (runs.length === 1) {
    const body = runs[0].body;
    process.stdout.write(body.endsWith("\n") ? body : `${body}\n`);
    return;
  }
  for (const r of runs) {
    process.stdout.write(`# ship: pattern=${r.patternId} sha256=${r.sha256}\n`);
    const body = r.body;
    process.stdout.write(body.endsWith("\n") ? body : `${body}\n`);
  }
}

/**
 * Print the workspace-policies preamble once at the top of stdout,
 * before the first pattern body. Trailing ``---`` separator visually
 * distinguishes the preamble from the pattern markdown the agent is
 * about to consume; an extra blank line above the separator keeps
 * the markdown well-formed if the preamble already ends with one.
 */
function emitPoliciesPreamble(preamble) {
  const trimmed = preamble.endsWith("\n") ? preamble : `${preamble}\n`;
  process.stdout.write(trimmed);
  process.stdout.write("\n---\n");
}

/**
 * Fetch the workspace prose-rule policies for the current run from
 * the Ship backend. The endpoint URL is derived from the callback
 * URL by swapping the trailing ``/result`` segment for
 * ``/policies-preamble`` — both share the same auth dependency
 * (per-run JWT or long-lived ``SHIP_RUN_TOKEN``), so we can reuse
 * the same bearer.
 *
 * Returns the preamble markdown or ``null`` when:
 *  - there's no callback URL / token (local invocation),
 *  - the URL doesn't end in ``/result`` (someone overrode the
 *    callback endpoint to a non-canonical path — too risky to
 *    guess),
 *  - the backend has no enabled policies (``preamble: null``),
 *  - or the request fails for any reason.
 *
 * Failures are surfaced as ``warn:`` lines on stderr so an operator
 * can debug them without breaking the lane execution.
 */
async function fetchPoliciesPreamble(args) {
  const callbackUrl = args.callbackUrl || process.env.SHIP_CALLBACK_URL;
  if (!callbackUrl) return null;
  const token = args.runToken || process.env.SHIP_RUN_TOKEN;
  if (!token) return null;
  if (!callbackUrl.endsWith("/result")) {
    console.error(
      `warn: SHIP_CALLBACK_URL does not end in /result; skipping policies-preamble fetch (got ${callbackUrl}).`,
    );
    return null;
  }
  const url = `${callbackUrl.slice(0, -"/result".length)}/policies-preamble`;
  try {
    const res = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
    if (!res.ok) {
      console.error(
        `warn: policies-preamble fetch returned HTTP ${res.status} ${res.statusText}; continuing without policies.`,
      );
      return null;
    }
    const body = await res.json().catch(() => null);
    if (!body || typeof body !== "object") return null;
    const preamble = body.preamble;
    if (typeof preamble !== "string" || !preamble.trim()) return null;
    return preamble;
  } catch (err) {
    console.error(
      `warn: policies-preamble fetch failed: ${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
}


/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function parseArgs(rest) {
  const out = {
    routine: null,
    pattern: null,
    fanout: null,
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
    if (str("--routine", "routine")) continue;
    if (str("--lane", "routine")) continue;
    if (str("--pattern", "pattern")) continue;
    if (str("--fanout", "fanout")) continue;
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
  if (out.fanout && !LANE_FANOUT_MODES.includes(out.fanout)) {
    die(
      EXIT_USAGE,
      `--fanout must be one of ${LANE_FANOUT_MODES.join("|")}; got ${out.fanout}`,
    );
  }
  if (out.pattern !== null && (typeof out.pattern !== "string" || !out.pattern.trim())) {
    die(EXIT_USAGE, "--pattern: must be a non-empty pattern id");
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
  /* --offline takes precedence when requested: we MUST NOT hit the
   * network or fall through to another source. The lockfile is the
   * single source of truth. This makes CI runs reproducible and keeps
   * air-gapped installs honest. */
  if (offline) return fetchFromLockfile({ patternId, root, strict: true });

  /* 1) Running inside the Ship monorepo — read from disk. */
  const shipRepo = resolveShipRepoRootForCatalog();
  if (shipRepo) {
    const file = readArtifactFile(shipRepo, "pattern", patternId);
    if (file) {
      const verification = verifyAgainstLockfile({
        root,
        patternId,
        body: file.content,
      });
      if (verification.warning) console.error(`warn: ${verification.warning}`);
      return { ok: true, body: file.content, source: "monorepo", lock: verification };
    }
  }

  /* 2) Network: same resolver `shipctl kickoff` uses. */
  const base = resolveMethodologyBase(ctx, config);
  try {
    const { content } = await fetchArtifact(base, "pattern", patternId, patternVersion || undefined);
    const verification = verifyAgainstLockfile({ root, patternId, body: content });
    if (verification.warning) console.error(`warn: ${verification.warning}`);
    return { ok: true, body: content, source: "http", lock: verification };
  } catch (err) {
    /* If the network call failed but we have a locked copy on disk, let
     * the operator fall back with a clear warning. This mirrors the
     * `npm install --offline` escape hatch when the registry is down. */
    const fallback = fetchFromLockfile({ patternId, root, strict: false });
    if (fallback.ok) {
      console.error(
        `warn: network fetch failed for pattern/${patternId}; using locked copy (${fallback.source}).`,
      );
      return fallback;
    }
    return {
      ok: false,
      error: `failed to fetch pattern ${patternId}: ${err instanceof Error ? err.message : err}`,
    };
  }
}

function verifyAgainstLockfile({ root, patternId, body }) {
  let lock;
  try {
    lock = readLockfile(root);
  } catch (err) {
    return { present: false, ok: null, warning: `lockfile unreadable: ${err.message}` };
  }
  if (!lock) return { present: false, ok: null };
  const entry = lookupLock(lock, "pattern", patternId);
  if (!entry) {
    return {
      present: true,
      ok: null,
      warning: `lockfile present but has no entry for pattern/${patternId}; run 'shipctl sync --lock'.`,
    };
  }
  const result = verifyBody(entry, body);
  if (!result.ok) {
    return {
      present: true,
      ok: false,
      reason: result.reason,
      expected: result.expected,
      actual: result.actual,
      warning: `pattern/${patternId} sha256 drift vs lockfile (${result.reason}; expected ${result.expected?.slice(0, 8)} got ${result.actual?.slice(0, 8)})`,
    };
  }
  return { present: true, ok: true, version: entry.version };
}

function fetchFromLockfile({ patternId, root, strict }) {
  let lock;
  try {
    lock = readLockfile(root);
  } catch (err) {
    return {
      ok: false,
      error: `lockfile unreadable: ${err.message}. Run 'shipctl sync --lock' to rebuild.`,
    };
  }
  if (!lock) {
    if (!strict) return { ok: false, error: "lockfile missing" };
    return {
      ok: false,
      error:
        "--offline requires .ship/shipctl.lock.json. Run 'shipctl sync --lock' in an online environment first.",
    };
  }
  const entry = lookupLock(lock, "pattern", patternId);
  if (!entry) {
    return {
      ok: false,
      error:
        strict
          ? `--offline: pattern/${patternId} missing from .ship/shipctl.lock.json. Run 'shipctl sync --lock' to re-resolve.`
          : `pattern/${patternId} not in lockfile`,
    };
  }
  const abs = path.join(root, entry.cached_path);
  let body;
  try {
    body = fs.readFileSync(abs, "utf8");
  } catch (err) {
    return {
      ok: false,
      error: `--offline: cached pattern body unreadable at ${entry.cached_path} (${err instanceof Error ? err.message : err}). Run 'shipctl sync --lock'.`,
    };
  }
  const verification = verifyBody(entry, body);
  if (!verification.ok) {
    return {
      ok: false,
      error: `--offline: sha256 mismatch for pattern/${patternId} (expected ${verification.expected?.slice(0, 8)}, got ${verification.actual?.slice(0, 8)}). Re-run 'shipctl sync --lock'.`,
    };
  }
  return {
    ok: true,
    body,
    source: "lockfile",
    lock: { present: true, ok: true, version: entry.version },
  };
}

function resolveMethodologyBase(ctx, config) {
  const fromFlag = ctx.baseUrl;
  const fromEnv =
    typeof process.env.SHIP_API_BASE === "string" && process.env.SHIP_API_BASE.trim()
      ? process.env.SHIP_API_BASE.trim().replace(/\/$/, "")
      : null;
  /* Wizard-seeded Actions secret: exact Ship API origin (``POST /fetch`` lives
   * at the root next to ``/v1``). Do not append ``/api/methodology`` here. */
  if (fromEnv) {
    return fromEnv;
  }
  const raw = config?.api?.base_url;
  if (typeof raw === "string" && raw.trim()) {
    const u = raw.replace(/\/$/, "");
    return u.includes("/api/methodology") ? u : `${u}/api/methodology`;
  }
  return fromFlag;
}

/*
 * Assemble the callback `metrics` bag so Ship's backend can tie each
 * run back to its lane + GitHub Actions run without re-parsing logs.
 *
 * Always-on breadcrumbs (iff we have the data):
 *   - lane_id             — id from `.ship/config.yml`; also recoverable
 *                           from the ship-<lane_id>.yml workflow path,
 *                           but duplicating here costs us nothing and
 *                           makes non-GitHub adapters (RFC-0007 Phase 8)
 *                           cheaper because they won't have that URL.
 *   - gh_workflow_run_id  — GITHUB_RUN_ID env (empty outside Actions).
 *   - gh_html_url         — constructed from GITHUB_SERVER_URL / _REPOSITORY
 *                           / _RUN_ID so the Console can deep-link the
 *                           GH UI from a Lane detail view.
 *   - gh_event            — GITHUB_EVENT_NAME (push / schedule / PR /…).
 *
 * Caller-supplied extras (pattern id / sha) stack on top. Nothing here
 * is required; the backend treats unknown keys as opaque forward-compat
 * payload.
 */
function collectCallbackMetrics(args, extra = {}) {
  const env = process.env;
  const out = { ...(extra || {}) };
  if (args && args.routine && !out.routine_id) out.routine_id = args.routine;
  if (args && args.routine && !out.lane_id) out.lane_id = args.routine;
  if (env.GITHUB_RUN_ID && !out.gh_workflow_run_id) {
    out.gh_workflow_run_id = env.GITHUB_RUN_ID;
  }
  if (env.GITHUB_SERVER_URL && env.GITHUB_REPOSITORY && env.GITHUB_RUN_ID && !out.gh_html_url) {
    out.gh_html_url = `${env.GITHUB_SERVER_URL}/${env.GITHUB_REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID}`;
  }
  if (env.GITHUB_EVENT_NAME && !out.gh_event) out.gh_event = env.GITHUB_EVENT_NAME;
  return out;
}

async function tryCallback(args, status, summary, extraMetrics = {}) {
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
  const metrics = collectCallbackMetrics(args, extraMetrics);
  if (Object.keys(metrics).length > 0) body.metrics = metrics;

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
      `# ship: routine=${summary.routine || summary.lane} status=${summary.status}${summary.reason ? ` reason="${summary.reason}"` : ""}`,
    );
  }
}

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}
