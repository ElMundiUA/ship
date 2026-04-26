import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {
  readConfig,
  readState,
  writeState,
  findShipRoot,
} from "../config/io.mjs";
import { validateConfig, lanePatterns as lanePatternList } from "../config/schema.mjs";
import { fetchManifest, fetchArtifact } from "../http.mjs";
import {
  readCached,
  writeCached,
  listCached,
  cachePath,
  verifyCachedOnDisk,
} from "../cache/store.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { readArtifactFile, scanArtifacts } from "../artifacts/fs-index.mjs";
import {
  writeLockfile,
  entryFromBody,
  lockKey,
  LOCKFILE_SCHEMA_VERSION,
} from "../state/lockfile.mjs";
import { getCliVersion } from "../version.mjs";

function printSyncHelp() {
  console.log(`shipctl sync — fetch the catalog into .ship/cache (and optionally lock).

WHAT THIS COMMAND DOES
  Pulls the artifacts your repo declares — pins, the active preset,
  per-agent rule collections, and any pattern referenced by your lanes
  (Automations in the operator console) — from the methodology API
  into .ship/cache/<kind>/<id>@<version>/. Verifies content_sha256,
  writes meta, optionally produces a lockfile so 'shipctl run --offline'
  is reproducible.

USAGE
  shipctl sync [--check-only] [--only <kind:id>]... [--channel <c>]
               [--force-unpin] [--dry-run] [--lock] [--json] [--cwd <dir>]

FLAGS
  --check-only         Report what would change; do not write to disk.
  --only <kind:id>     Restrict to one or more artifacts (repeatable).
                       Example: --only pattern:role-developer --only collection:preset-web-app
  --channel <c>        Override config.api.channel for this invocation
                       (stable|edge).
  --force-unpin        Ignore artifacts.pins[] and pull the manifest
                       version. Use when intentionally bumping a pin.
  --dry-run            Print the resolution plan; do not write or fetch.
  --lock               After sync, materialise every pattern referenced
                       by the declared routines and write
                       .ship/shipctl.lock.json (used by
                       'shipctl run --offline').
  --json               Emit a structured JSON summary on stdout.
  --cwd <dir>          Repo root. Defaults to the current directory;
                       searches upward for .ship/.
  --help, -h           Show this help.

EXAMPLES
  shipctl sync                          # baseline pull
  shipctl sync --check-only --json      # CI guard
  shipctl sync --only pattern:role-developer --only tool:methodology-api
  shipctl sync --lock                   # produce a reproducible lockfile

EXIT CODE
  0 when everything resolved.
  20 when at least one artifact failed to fetch (or --lock left
     unresolved entries).
`);
}

function parseSyncArgs(rest) {
  const out = {
    cwd: process.cwd(),
    checkOnly: false,
    dryRun: false,
    forceUnpin: false,
    channel: null,
    only: [],
    lock: false,
    json: false,
    help: false,
    unknown: [],
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") {
      out.help = true;
      copy.shift();
      continue;
    }
    if (a === "--check-only") {
      out.checkOnly = true;
      copy.shift();
      continue;
    }
    if (a === "--dry-run") {
      out.dryRun = true;
      copy.shift();
      continue;
    }
    if (a === "--force-unpin") {
      out.forceUnpin = true;
      copy.shift();
      continue;
    }
    if (a === "--lock") {
      out.lock = true;
      copy.shift();
      continue;
    }
    if (a === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (a === "--channel" && copy[1]) {
      copy.shift();
      out.channel = copy.shift();
      continue;
    }
    if (a.startsWith("--channel=")) {
      out.channel = a.slice("--channel=".length);
      copy.shift();
      continue;
    }
    if (a === "--only" && copy[1]) {
      copy.shift();
      out.only.push(copy.shift());
      continue;
    }
    if (a.startsWith("--only=")) {
      out.only.push(a.slice("--only=".length));
      copy.shift();
      continue;
    }
    if (a === "--cwd" && copy[1]) {
      copy.shift();
      out.cwd = copy.shift();
      continue;
    }
    if (a.startsWith("--cwd=")) {
      out.cwd = a.slice("--cwd=".length);
      copy.shift();
      continue;
    }
    /* Previously we silently dropped unrecognised tokens here. That
     * hid bashisms like a misspelt `--cheek-only`, so we now collect
     * them and warn from `syncCommand` once parsing is complete.
     * Stays non-fatal because existing CI scripts may rely on the old
     * permissive behaviour. */
    out.unknown.push(a);
    copy.shift();
  }
  return out;
}

function parseOnlySpec(spec) {
  const idx = spec.indexOf(":");
  if (idx <= 0) return null;
  return { kind: spec.slice(0, idx), id: spec.slice(idx + 1) };
}

function pinSatisfies(pin, version) {
  if (!pin) return true;
  const p = pin.trim();
  if (p === version) return true;
  if (/^\d+(\.\d+){0,2}$/.test(p)) return version.startsWith(p);
  // Caret / tilde / comparators: conservative match on major.minor for ranges.
  const m = p.match(/^(\^|~)(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
  if (m) {
    const [, op, maj, min] = m;
    const parts = version.split(".");
    if (parts[0] !== maj) return false;
    if (op === "~" && min !== undefined && parts[1] !== min) return false;
    return true;
  }
  return false;
}

function hoursSince(iso) {
  if (!iso) return Infinity;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return Infinity;
  return (Date.now() - ts) / (1000 * 60 * 60);
}

function manifestHash(entries) {
  const canonical = JSON.stringify(
    entries.map((e) => ({
      kind: e.kind,
      id: e.id,
      version: e.version,
      content_sha256: e.content_sha256,
    })),
  );
  return crypto.createHash("sha256").update(canonical).digest("hex");
}

function appendTelemetryEvent(shipRoot, config, event) {
  if (!config.telemetry || config.telemetry.share !== true) return;
  const file = path.join(shipRoot, ".ship", "telemetry-outbox.jsonl");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const envelope = {
    event: event.event,
    ts: new Date().toISOString(),
    anonymous_id: config.telemetry.anonymous_id,
    shipctl_version: "0.9.0",
    stack_preset: config.stack?.preset || null,
    payload: event.payload,
  };
  fs.appendFileSync(file, `${JSON.stringify(envelope)}\n`, "utf8");
}

/**
 * Build the set of desired {kind,id} targets given config + cache + manifest.
 */
function computeDesired(config, manifestEntries, cached, onlySpecs) {
  if (onlySpecs.length > 0) {
    const specs = onlySpecs.map(parseOnlySpec).filter(Boolean);
    return manifestEntries.filter((e) =>
      specs.some((s) => s.kind === e.kind && s.id === e.id),
    );
  }

  const wanted = new Map();
  const add = (e) => {
    const key = `${e.kind}:${e.id}`;
    if (!wanted.has(key)) wanted.set(key, e);
  };

  const pins = config.artifacts?.pins || {};
  for (const pinKey of Object.keys(pins)) {
    const slash = pinKey.indexOf("/");
    if (slash < 0) continue;
    const kind = pinKey.slice(0, slash);
    const id = pinKey.slice(slash + 1);
    const entry = manifestEntries.find((e) => e.kind === kind && e.id === id);
    if (entry) add(entry);
  }

  for (const c of cached) {
    const entry = manifestEntries.find((e) => e.kind === c.kind && e.id === c.id);
    if (entry) add(entry);
  }

  const preset = config.stack?.preset;
  if (preset) {
    const presetCollectionId = `preset-${preset}`;
    const e = manifestEntries.find(
      (m) => m.kind === "collection" && m.id === presetCollectionId,
    );
    if (e) add(e);
  }

  for (const agent of config.stack?.agents || []) {
    const e = manifestEntries.find(
      (m) => m.kind === "collection" && m.id === `agent-rules/${agent}`,
    );
    if (e) add(e);
  }

  return [...wanted.values()];
}

/**
 * Reusable sync implementation, suitable for embedding from other CLI
 * commands (notably `shipctl init`). Returns a structured summary instead
 * of calling `process.exit`.
 *
 * @typedef {Object} SyncOptions
 * @property {string} [cwd]
 * @property {string} [baseUrl]
 * @property {string} [channel]
 * @property {boolean} [dryRun]
 * @property {boolean} [checkOnly]
 * @property {boolean} [forceUnpin]
 * @property {string[]} [only]           "kind:id" specs; overrides config-derived desired set
 * @property {Array<string|{kind:string,id:string}>} [include]
 *           Additional specs merged with `only` (kept separate so callers
 *           can reason about them independently).
 * @property {string[]} [onlyKinds]      Optional post-filter on manifest entries (kind whitelist)
 * @property {boolean} [verbose]         When true, write human progress to stdout (default: CLI only)
 *
 * @returns {Promise<{
 *   up_to_date:number, updated:number, skipped_pin:number,
 *   deprecated:number, yanked:number, failed:number,
 *   notes:string[], entries:Array<{kind:string,id:string,version:string,action:string}>
 * }>}
 */
export async function syncArtifacts(options = {}) {
  const {
    cwd = process.cwd(),
    baseUrl: baseUrlOpt,
    channel: channelOpt,
    dryRun = false,
    checkOnly = false,
    forceUnpin = false,
    only = [],
    include = [],
    onlyKinds = null,
    verbose = false,
  } = options;

  const root = findShipRoot(cwd);
  if (!root) {
    const err = new Error(".ship/ not found. Run 'shipctl config init' first.");
    err.exitCode = 10;
    throw err;
  }

  const { config } = readConfig(root);
  const valid = validateConfig(config);
  if (!valid.ok) {
    const err = new Error(valid.errors.join("\n"));
    err.exitCode = 10;
    throw err;
  }
  if (verbose) for (const w of valid.warnings) console.error(`warn: ${w}`);

  const baseUrl = (
    baseUrlOpt ||
    process.env.SHIP_API_BASE ||
    config.api?.base_url ||
    "https://ship.elmundi.com"
  ).replace(/\/$/, "");
  const channel = channelOpt || process.env.SHIP_CHANNEL || config.api?.channel || "stable";
  const ttlHours =
    typeof config.api?.ttl_hours === "number" ? config.api.ttl_hours : 24;

  if (dryRun && verbose) {
    console.log(
      `plan: GET ${baseUrl}/{patterns,tools,collections} (channel=${channel})`,
    );
  }

  let manifestEntries;
  try {
    manifestEntries = await fetchManifest(baseUrl, { channel });
  } catch (e) {
    const err = new Error(e.message);
    err.exitCode = 20;
    throw err;
  }

  if (Array.isArray(onlyKinds) && onlyKinds.length) {
    manifestEntries = manifestEntries.filter((m) => onlyKinds.includes(m.kind));
  }

  const { state } = readState(root);
  const cached = listCached(root);

  // Normalise "include" into "kind:id" strings, merged with `only`.
  const mergedOnly = [
    ...only,
    ...include
      .map((e) => (typeof e === "string" ? e : e && e.kind && e.id ? `${e.kind}:${e.id}` : null))
      .filter(Boolean),
  ];

  const desired = computeDesired(config, manifestEntries, cached, mergedOnly);

  const summary = {
    up_to_date: 0,
    updated: 0,
    skipped_pin: 0,
    deprecated: 0,
    yanked: 0,
    failed: 0,
  };
  /** @type {string[]} */
  const notes = [];
  /** @type {Array<{kind:string,id:string,version:string,action:string}>} */
  const entries = [];
  const pins = config.artifacts?.pins || {};

  for (const entry of desired) {
    const key = `${entry.kind}/${entry.id}`;
    if (entry.yanked === true) {
      summary.yanked += 1;
      notes.push(`yanked: ${key}@${entry.version}`);
      entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "yanked" });
      continue;
    }
    if (entry.deprecated === true) {
      const isPinned = Object.prototype.hasOwnProperty.call(pins, key);
      summary.deprecated += 1;
      notes.push(
        `deprecated: ${key}@${entry.version}${entry.replaced_by ? ` → ${entry.replaced_by}` : ""}`,
      );
      if (!isPinned) {
        entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "deprecated" });
        continue;
      }
    }

    const pin = pins[key];
    if (pin && !pinSatisfies(pin, entry.version) && !forceUnpin) {
      summary.skipped_pin += 1;
      notes.push(`skipped_pin: ${key} pinned=${pin} upstream=${entry.version}`);
      entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "skipped_pin" });
      continue;
    }

    const localAll = cached.filter((c) => c.kind === entry.kind && c.id === entry.id);
    const sameVersion = localAll.find((c) => c.version === entry.version);
    if (sameVersion) {
      const existingHash = sameVersion.sha256 === entry.content_sha256;
      const age = hoursSince(sameVersion.fetched_at);
      if (existingHash && age < ttlHours) {
        // Physical-presence & integrity guard: meta.json alone is not enough
        // — the rendered body may have been deleted or edited in place, in
        // which case we must force a re-fetch so the cache matches disk.
        const onDisk = verifyCachedOnDisk(root, entry.kind, entry.id, entry.version);
        if (onDisk.ok) {
          summary.up_to_date += 1;
          entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "up_to_date" });
          continue;
        }
        notes.push(
          `refetch: ${key}@${entry.version} (${onDisk.reason === "missing_body" ? "missing" : onDisk.reason === "drift" ? "drifted" : onDisk.reason || "invalid"})`,
        );
        // Fall through to the fetch branch below (respect checkOnly/dryRun).
      }
    }

    if (checkOnly || dryRun) {
      summary.updated += 1;
      entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "would_update" });
      if (dryRun && verbose) {
        console.log(`plan: POST ${baseUrl}/fetch ${JSON.stringify({ kind: entry.kind, id: entry.id, version: entry.version })}`);
      }
      continue;
    }

    try {
      const { content, meta } = await fetchArtifact(baseUrl, entry.kind, entry.id, entry.version);
      if (entry.content_sha256 && meta.content_sha256 !== entry.content_sha256) {
        summary.failed += 1;
        notes.push(
          `failed: ${key}@${entry.version} content_sha256 mismatch (manifest=${entry.content_sha256} got=${meta.content_sha256})`,
        );
        entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "failed" });
        continue;
      }
      writeCached(root, entry.kind, entry.id, entry.version, content, {
        ...meta,
        updated_at: entry.updated_at || meta.updated_at,
        channel: entry.channel || meta.channel,
      });
      summary.updated += 1;
      entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "updated" });
    } catch (e) {
      summary.failed += 1;
      notes.push(`failed: ${key}@${entry.version}: ${e.message}`);
      entries.push({ kind: entry.kind, id: entry.id, version: entry.version, action: "failed" });
    }
  }

  if (!checkOnly && !dryRun) {
    const newState = {
      ...state,
      last_sync_at: new Date().toISOString(),
      last_manifest_hash: manifestHash(manifestEntries),
    };
    writeState(root, newState);

    appendTelemetryEvent(root, config, {
      event: "artifact.sync",
      payload: {
        categories: [...new Set(desired.map((e) => e.kind))].sort(),
        updates_count: summary.updated,
        failures_count: summary.failed,
      },
    });
  }

  return { ...summary, notes, entries };
}

/**
 * Produce a lockfile covering every pattern the config's lanes depend on,
 * plus any pattern the config pins explicitly. Other artifact kinds are
 * out of scope today — lanes only reference patterns, and pins for tools
 * or collections don't need reproducibility guarantees at run-time (yet).
 *
 * Resolution order per pattern:
 *   1. `.ship/cache/pattern/<id>@<v>/ARTIFACT.md` (materialised by sync).
 *   2. Ship monorepo fallback (`artifacts/patterns/<id>/ARTIFACT.md`).
 *   3. One-shot POST /fetch to the methodology API.
 *
 * Returns a structured report instead of writing to disk directly so the
 * caller can roll it into the overall sync summary and fail the job on
 * unresolved patterns.
 *
 * @param {Object} opts
 * @param {string} opts.shipRoot
 * @param {Object} opts.config
 * @param {string} opts.baseUrl
 * @param {string} opts.channel
 * @param {boolean} [opts.verbose]
 * @returns {Promise<{ lockfile:object, resolved:Array<object>, unresolved:Array<object>, notes:string[] }>}
 */
export async function buildLockfile({ shipRoot, config, baseUrl, channel, verbose = false }) {
  /** @type {Record<string, object>} */
  const artifacts = {};
  /** @type {Array<{kind:string,id:string,version:string,source:string,pinned:boolean}>} */
  const resolved = [];
  /** @type {Array<{kind:string,id:string,reason:string}>} */
  const unresolved = [];
  /** @type {string[]} */
  const notes = [];

  const pins = config.artifacts?.pins || {};
  /* Flatten each lane into one (laneId, patternId) row per pattern so
   * lanes that declare ``patterns: [a, b]`` (RFC-0008 C3.1) feed both
   * into the sync/lockfile pipeline. Legacy ``pattern: <id>`` lanes
   * normalise to a single-element list via lanePatternList(). */
  const laneRows = [];
  for (const [laneId, lane] of Object.entries(config.lanes || {})) {
    for (const pid of lanePatternList(lane)) {
      laneRows.push({ laneId, patternId: pid });
    }
  }
  const pinRows = Object.keys(pins)
    .filter((k) => k.startsWith("pattern/"))
    .map((k) => ({ laneId: null, patternId: k.slice("pattern/".length) }));

  /* De-duplicate on pattern id while preserving lane provenance (useful
   * for the `notes` field — operators want to know which lane pinned a
   * given pattern when they read the diff). */
  const seen = new Map();
  for (const row of [...laneRows, ...pinRows]) {
    const pid = row.patternId;
    if (!seen.has(pid)) seen.set(pid, { id: pid, by: [] });
    seen.get(pid).by.push(row.laneId || "config.artifacts.pins");
  }

  const shipRepo = resolveShipRepoRootForCatalog();
  const cached = listCached(shipRoot);

  for (const [patternId, ctx] of seen) {
    const pinKey = `pattern/${patternId}`;
    const isPinned = Object.prototype.hasOwnProperty.call(pins, pinKey);

    /* 1) Look for an already-cached copy. */
    const localAll = cached.filter((c) => c.kind === "pattern" && c.id === patternId);
    if (localAll.length) {
      localAll.sort((a, b) => cmpSemver(b.version, a.version));
      const latest = localAll[0];
      const body = readCached(shipRoot, "pattern", patternId, latest.version);
      if (body && body.content) {
        artifacts[lockKey("pattern", patternId)] = entryFromBody({
          body: body.content,
          version: latest.version,
          cachedPath: path.relative(
            shipRoot,
            cachePath(shipRoot, "pattern", patternId, latest.version),
          ),
          source: "http",
          pinned: isPinned,
          channel: body.meta?.channel || channel,
        });
        resolved.push({
          kind: "pattern",
          id: patternId,
          version: latest.version,
          source: "cache",
          pinned: isPinned,
          lanes: ctx.by,
        });
        continue;
      }
    }

    /* 2) Running inside the Ship monorepo — read from artifacts/ and
     * materialise the body into the customer's local cache so the
     * lockfile's `cached_path` is always inside ship_root. This keeps
     * `shipctl run --offline` working without SHIP_REPO set at run
     * time (important for `act`-style local CI reproductions and
     * enterprise forks where the monorepo isn't on the runner). */
    if (shipRepo) {
      const file = readArtifactFile(shipRepo, "pattern", patternId);
      if (file) {
        const version = parseVersionFromFrontmatter(file.content) || "0.0.0-monorepo";
        writeCached(shipRoot, "pattern", patternId, version, file.content, {
          kind: "pattern",
          id: patternId,
          version,
          channel,
        });
        const cachedAbs = cachePath(shipRoot, "pattern", patternId, version);
        artifacts[lockKey("pattern", patternId)] = entryFromBody({
          body: file.content,
          version,
          cachedPath: path.relative(shipRoot, cachedAbs).replace(/\\/g, "/"),
          source: "monorepo",
          pinned: isPinned,
          channel,
        });
        resolved.push({
          kind: "pattern",
          id: patternId,
          version,
          source: "monorepo",
          pinned: isPinned,
          lanes: ctx.by,
        });
        continue;
      }
    }

    /* 3) Last resort — fetch fresh from the API. We can't cache-write
     * without a known version, so only the sha256 + body go into the
     * lockfile; subsequent `shipctl sync --lock` runs will promote it
     * into the cache on the normal sync pass. */
    try {
      const pin = pins[pinKey];
      const { content, meta } = await fetchArtifact(baseUrl, "pattern", patternId, pin || undefined);
      const version = meta.version || "0.0.0";
      // Promote into cache immediately so subsequent --offline runs find it.
      writeCached(shipRoot, "pattern", patternId, version, content, {
        ...meta,
        channel: meta.channel || channel,
      });
      artifacts[lockKey("pattern", patternId)] = entryFromBody({
        body: content,
        version,
        cachedPath: path.relative(
          shipRoot,
          cachePath(shipRoot, "pattern", patternId, version),
        ),
        source: "http",
        pinned: isPinned,
        channel: meta.channel || channel,
      });
      resolved.push({
        kind: "pattern",
        id: patternId,
        version,
        source: "http",
        pinned: isPinned,
        lanes: ctx.by,
      });
    } catch (err) {
      unresolved.push({
        kind: "pattern",
        id: patternId,
        reason: err instanceof Error ? err.message : String(err),
      });
      notes.push(`unresolved: pattern/${patternId}: ${err instanceof Error ? err.message : err}`);
      if (verbose) {
        console.error(
          `warn: lock: could not resolve pattern/${patternId}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  }

  const lockfile = {
    version: LOCKFILE_SCHEMA_VERSION,
    generated_at: new Date().toISOString(),
    shipctl_version: getCliVersion(),
    source: { base_url: baseUrl, channel },
    artifacts,
    notes: notes.slice(),
  };

  return { lockfile, resolved, unresolved, notes };
}

function parseVersionFromFrontmatter(content) {
  if (!content.startsWith("---")) return null;
  const end = content.indexOf("\n---", 3);
  if (end < 0) return null;
  const header = content.slice(3, end);
  const m = header.match(/^version:\s*['"]?([^'"\n]+)['"]?/m);
  return m ? m[1].trim() : null;
}

function cmpSemver(a, b) {
  const parts = (s) =>
    String(s)
      .split(/[.-]/)
      .map((x) => (Number.isNaN(Number(x)) ? x : Number(x)));
  const pa = parts(a);
  const pb = parts(b);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const xa = pa[i];
    const xb = pb[i];
    if (xa === undefined) return -1;
    if (xb === undefined) return 1;
    if (xa === xb) continue;
    if (typeof xa === typeof xb) return xa < xb ? -1 : 1;
    return typeof xa === "number" ? -1 : 1;
  }
  return 0;
}

export async function syncCommand(ctx, rest) {
  const args = parseSyncArgs(rest);
  if (args.help) {
    printSyncHelp();
    return;
  }
  if (ctx?.dryRun) args.dryRun = true;
  if (ctx?.json) args.json = true;
  for (const tok of args.unknown) {
    console.warn(
      `warn: shipctl sync: ignoring unknown argument '${tok}'. Run 'shipctl sync --help'.`,
    );
  }

  let result;
  try {
    result = await syncArtifacts({
      cwd: args.cwd,
      baseUrl: ctx?.baseUrl,
      channel: args.channel,
      dryRun: args.dryRun,
      checkOnly: args.checkOnly,
      forceUnpin: args.forceUnpin,
      only: args.only,
      verbose: !args.json,
    });
  } catch (e) {
    /* When `--lock` is requested we treat manifest failures as soft:
     * the lockfile build has its own resolution chain (cache → monorepo
     * → HTTP) and will report its own unresolved entries. This keeps
     * `shipctl sync --lock` useful for customers who only run Ship-
     * locally (e.g. internal forks) or are offline with a mirrored
     * monorepo on SHIP_REPO. */
    if (!args.lock) {
      const code = typeof e.exitCode === "number" ? e.exitCode : 1;
      console.error(e.message);
      process.exit(code);
    }
    if (!args.json) console.error(`warn: manifest sync skipped (${e.message || e})`);
    result = {
      up_to_date: 0,
      updated: 0,
      skipped_pin: 0,
      deprecated: 0,
      yanked: 0,
      failed: 0,
      notes: [`manifest sync skipped (${e.message || e})`],
      entries: [],
    };
  }

  /* --lock: walk the lane patterns, make sure every body is materialised
   * under .ship/cache, and dump a lockfile so `shipctl run --offline` has
   * a content-sha to compare against. Only runs after a successful-ish
   * normal sync (we don't care if individual artifacts failed upstream —
   * lockfile generation has its own fallback chain). */
  let lockResult = null;
  if (args.lock && !args.dryRun && !args.checkOnly) {
    const shipRoot = findShipRoot(args.cwd);
    if (!shipRoot) {
      console.error("--lock: .ship/ not found; run 'shipctl init' first.");
      process.exit(10);
    }
    const { config } = readConfig(shipRoot);
    const baseUrl = (
      ctx?.baseUrl ||
      process.env.SHIP_API_BASE ||
      config.api?.base_url ||
      "https://ship.elmundi.com"
    ).replace(/\/$/, "");
    const channel = args.channel || process.env.SHIP_CHANNEL || config.api?.channel || "stable";
    try {
      lockResult = await buildLockfile({
        shipRoot,
        config,
        baseUrl,
        channel,
        verbose: !args.json,
      });
      writeLockfile(shipRoot, lockResult.lockfile);
    } catch (err) {
      console.error(`--lock: ${err instanceof Error ? err.message : err}`);
      process.exit(20);
    }
  }

  if (args.json) {
    const payload = { ...result };
    if (lockResult) {
      payload.lock = {
        path: path.join(".ship", "shipctl.lock.json"),
        entries: Object.keys(lockResult.lockfile.artifacts).length,
        resolved: lockResult.resolved,
        unresolved: lockResult.unresolved,
      };
    }
    console.log(JSON.stringify(payload, null, 2));
  } else {
    const lines = [
      `up_to_date: ${result.up_to_date}`,
      `updated:    ${result.updated}`,
      `skipped_pin:${result.skipped_pin}`,
      `deprecated: ${result.deprecated}${result.deprecated ? " (…)" : ""}`,
      `yanked:     ${result.yanked}`,
      `failed:     ${result.failed}`,
    ];
    for (const l of lines) console.log(l);
    for (const n of result.notes) console.log(`  - ${n}`);

    if (lockResult) {
      const entryCount = Object.keys(lockResult.lockfile.artifacts).length;
      console.log(
        `lock:       wrote .ship/shipctl.lock.json (${entryCount} entries, ${lockResult.unresolved.length} unresolved)`,
      );
      for (const n of lockResult.notes) console.log(`  - ${n}`);
    }
  }

  if (result.failed > 0) process.exit(20);
  if (lockResult && lockResult.unresolved.length > 0) process.exit(20);
}
