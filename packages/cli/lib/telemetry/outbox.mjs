import fs from "node:fs";
import path from "node:path";
import { readConfig } from "../config/io.mjs";

export const ALLOWED_EVENT_TYPES = Object.freeze([
  "artifact.fetch",
  "artifact.use",
  "artifact.sync",
  "feedback.submit",
  "doctor.result",
]);

export const DENYLIST_KEYS = Object.freeze([
  "path",
  "code",
  "diff",
  "branch",
  "remote",
  "email",
]);

const ALLOWED_SET = new Set(ALLOWED_EVENT_TYPES);
const DENY_SET = new Set(DENYLIST_KEYS.map((k) => k.toLowerCase()));

export function outboxPath(shipRoot) {
  return path.join(shipRoot, ".ship", "telemetry-outbox.jsonl");
}

function debug(msg) {
  if (process.env.SHIP_DEBUG === "1") {
    console.error(`[ship:telemetry] ${msg}`);
  }
}

/**
 * Recursively strip denylisted keys from a payload object, returning a new object.
 * Returns {stripped, removed: string[]}.
 */
function scrubPayload(payload) {
  const removed = [];
  function walk(node) {
    if (node === null || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map(walk);
    const out = {};
    for (const [k, v] of Object.entries(node)) {
      if (typeof k === "string" && DENY_SET.has(k.toLowerCase())) {
        removed.push(k);
        continue;
      }
      out[k] = walk(v);
    }
    return out;
  }
  const stripped = walk(payload ?? {});
  return { stripped, removed };
}

/**
 * Normalize an incoming event into the backend-compatible envelope:
 *   { type, anonymous_id, timestamp, payload, shipctl_version?, stack_preset? }
 *
 * Accepts both the old shape ({event, ts}) emitted by earlier sync code and the
 * new shape ({type, timestamp}). Only `type` is considered authoritative for
 * whitelist validation.
 */
function normalizeEvent(input) {
  if (!input || typeof input !== "object") {
    throw new Error("appendEvent: event must be an object");
  }
  const type = input.type || input.event;
  const timestamp = input.timestamp || input.ts || new Date().toISOString();
  const payload = input.payload || {};
  const out = {
    type,
    anonymous_id: input.anonymous_id,
    timestamp,
    payload,
  };
  if (input.shipctl_version) out.shipctl_version = input.shipctl_version;
  if (input.stack_preset !== undefined) out.stack_preset = input.stack_preset;
  return out;
}

function readConfigSafe(shipRoot) {
  try {
    const { config } = readConfig(shipRoot);
    return config;
  } catch {
    return null;
  }
}

/**
 * Append one event to the outbox. Silently no-ops when telemetry is disabled.
 * Throws on unknown event type. Strips denylisted keys from payload (quietly
 * logging under SHIP_DEBUG=1).
 *
 * @param {string} shipRoot
 * @param {object} event
 * @returns {boolean} true if appended, false if skipped (telemetry off).
 */
export function appendEvent(shipRoot, event) {
  const normalized = normalizeEvent(event);
  if (!normalized.type || !ALLOWED_SET.has(normalized.type)) {
    throw new Error(
      `appendEvent: unknown event type ${JSON.stringify(normalized.type)}; allowed=${ALLOWED_EVENT_TYPES.join(",")}`,
    );
  }

  const cfg = readConfigSafe(shipRoot);
  if (!cfg || cfg.telemetry?.share !== true) return false;
  if (String(process.env.SHIP_TELEMETRY || "").toLowerCase() === "false") return false;

  if (!normalized.anonymous_id) {
    normalized.anonymous_id = cfg.telemetry?.anonymous_id || null;
  }
  if (!normalized.anonymous_id) return false;

  const { stripped, removed } = scrubPayload(normalized.payload);
  if (removed.length) {
    debug(`stripped denylisted keys from ${normalized.type}: ${removed.join(", ")}`);
  }
  normalized.payload = stripped;

  const file = outboxPath(shipRoot);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFileSync(file, `${JSON.stringify(normalized)}\n`, "utf8");
  return true;
}

/**
 * Read and parse events from the outbox. Lines that fail to parse are skipped
 * with a debug warning. Old-shape events (`event`/`ts`) are upgraded on read.
 */
export function listEvents(shipRoot) {
  const file = outboxPath(shipRoot);
  if (!fs.existsSync(file)) return [];
  const raw = fs.readFileSync(file, "utf8");
  const out = [];
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      debug(`skipping malformed line in ${file}`);
      continue;
    }
    const upgraded = {
      type: parsed.type || parsed.event,
      anonymous_id: parsed.anonymous_id,
      timestamp: parsed.timestamp || parsed.ts,
      payload: parsed.payload || {},
    };
    if (parsed.shipctl_version) upgraded.shipctl_version = parsed.shipctl_version;
    if (parsed.stack_preset !== undefined) upgraded.stack_preset = parsed.stack_preset;
    out.push(upgraded);
  }
  return out;
}

export function countEvents(shipRoot) {
  return listEvents(shipRoot).length;
}

/**
 * Remove events older than `before` (ISO string). If `before` is omitted,
 * clears the entire outbox. Returns the number of events removed.
 */
export function clearEvents(shipRoot, { before } = {}) {
  const file = outboxPath(shipRoot);
  if (!fs.existsSync(file)) return 0;
  if (!before) {
    const before_count = countEvents(shipRoot);
    try {
      fs.unlinkSync(file);
    } catch {
      fs.writeFileSync(file, "", "utf8");
    }
    return before_count;
  }

  const beforeMs = Date.parse(before);
  if (Number.isNaN(beforeMs)) {
    throw new Error(`clearEvents: invalid 'before' timestamp ${JSON.stringify(before)}`);
  }
  const kept = [];
  let removed = 0;
  for (const ev of listEvents(shipRoot)) {
    const ts = Date.parse(ev.timestamp || "");
    if (!Number.isNaN(ts) && ts < beforeMs) {
      removed += 1;
    } else {
      kept.push(ev);
    }
  }
  if (kept.length === 0) {
    try {
      fs.unlinkSync(file);
    } catch {
      fs.writeFileSync(file, "", "utf8");
    }
  } else {
    const text = kept.map((e) => JSON.stringify(e)).join("\n") + "\n";
    fs.writeFileSync(file, text, "utf8");
  }
  return removed;
}

/**
 * Overwrite the outbox with the given list of event envelopes. Used by flush
 * to persist the subset of lines that failed to POST.
 */
export function writeAllEvents(shipRoot, events) {
  const file = outboxPath(shipRoot);
  if (!events.length) {
    if (fs.existsSync(file)) fs.unlinkSync(file);
    return;
  }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const text = events.map((e) => JSON.stringify(e)).join("\n") + "\n";
  fs.writeFileSync(file, text, "utf8");
}
