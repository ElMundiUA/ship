import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {
  readConfig,
  readState,
  writeState,
  findShipRoot,
} from "../config/io.mjs";
import { validateConfig } from "../config/schema.mjs";
import { fetchManifest, fetchArtifact } from "../http.mjs";
import {
  readCached,
  writeCached,
  listCached,
  cachePath,
  verifyCachedOnDisk,
} from "../cache/store.mjs";

function parseSyncArgs(rest) {
  const out = {
    cwd: process.cwd(),
    checkOnly: false,
    dryRun: false,
    forceUnpin: false,
    channel: null,
    only: [],
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
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
      `plan: GET ${baseUrl}/{patterns,workflows,tools,collections} (channel=${channel})`,
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

export async function syncCommand(ctx, rest) {
  const args = parseSyncArgs(rest);
  if (ctx?.dryRun) args.dryRun = true;

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
      verbose: true,
    });
  } catch (e) {
    const code = typeof e.exitCode === "number" ? e.exitCode : 1;
    console.error(e.message);
    process.exit(code);
  }

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

  if (result.failed > 0) process.exit(20);
}
