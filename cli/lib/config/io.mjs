import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import YAML from "yaml";
import { DEFAULT_CONFIG } from "./schema.mjs";

export const SHIP_DIR = ".ship";
export const CONFIG_REL = path.join(SHIP_DIR, "config.yml");
export const STATE_REL = path.join(SHIP_DIR, "state.json");

/**
 * Walk upward from startCwd looking for `.ship/config.yml`.
 * Returns the directory containing `.ship/` or null.
 * @param {string} startCwd
 * @returns {string | null}
 */
export function findShipRoot(startCwd) {
  let dir = path.resolve(startCwd || process.cwd());
  for (;;) {
    if (fs.existsSync(path.join(dir, CONFIG_REL))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/**
 * Stable top-level and nested key order. Unknown keys are appended alphabetically.
 */
const KEY_ORDER = {
  __root: ["version", "shipctl_min", "api", "stack", "artifacts", "cache", "telemetry"],
  api: ["base_url", "channel", "ttl_hours", "offline_ok"],
  stack: ["tracker", "ci", "agents", "agent", "language", "preset"],
  "stack.agent": ["provider"],
  artifacts: ["pins", "auto_update"],
  cache: ["vcs_tracked"],
  telemetry: ["share", "anonymous_id", "scope"],
  "telemetry.scope": ["artifact_usage", "improvement_drafts", "errors"],
};

function orderedCopy(obj, pathKey) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return obj;
  const order = KEY_ORDER[pathKey] || [];
  const remaining = new Set(Object.keys(obj));
  const out = {};
  for (const k of order) {
    if (remaining.has(k)) {
      out[k] = obj[k];
      remaining.delete(k);
    }
  }
  for (const k of [...remaining].sort()) out[k] = obj[k];

  for (const [k, v] of Object.entries(out)) {
    const childKey = pathKey === "__root" ? k : pathKey ? `${pathKey}.${k}` : k;
    if (childKey === "artifacts.pins") continue;
    out[k] = orderedCopy(v, childKey);
  }
  return out;
}

/**
 * @param {string} cwd
 * @returns {{config:object, filePath:string}}
 */
export function readConfig(cwd) {
  const root = findShipRoot(cwd);
  if (!root) {
    throw new Error(
      `.ship/config.yml not found (searched from ${path.resolve(cwd || process.cwd())} upward). Run 'shipctl config init' first.`,
    );
  }
  const filePath = path.join(root, CONFIG_REL);
  let text;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch (e) {
    throw new Error(`Failed to read ${filePath}: ${e.message}`);
  }
  let parsed;
  try {
    parsed = YAML.parse(text);
  } catch (e) {
    throw new Error(`Failed to parse ${filePath}: ${e.message}`);
  }
  if (!parsed || typeof parsed !== "object") {
    throw new Error(`${filePath}: top-level must be a YAML mapping`);
  }
  return { config: parsed, filePath };
}

/**
 * @param {string} filePath
 * @param {object} config
 */
export function writeConfig(filePath, config) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const ordered = orderedCopy(JSON.parse(JSON.stringify(config)), "__root");
  const body = YAML.stringify(ordered, {
    lineWidth: 0,
    indent: 2,
    defaultStringType: "PLAIN",
  });
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, body, "utf8");
  fs.renameSync(tmp, filePath);
}

/**
 * Generate a fresh UUID v4 into config.telemetry.anonymous_id if missing/invalid.
 * Mutates the config in place.
 * @param {object} config
 * @returns {string} the resulting anonymous_id
 */
export function ensureAnonymousId(config) {
  if (!config.telemetry || typeof config.telemetry !== "object") config.telemetry = {};
  const cur = config.telemetry.anonymous_id;
  const valid =
    typeof cur === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(cur);
  if (!valid) config.telemetry.anonymous_id = randomUUID();
  return config.telemetry.anonymous_id;
}

/**
 * Default empty state for .ship/state.json.
 */
export function defaultState() {
  return {
    last_sync_at: null,
    last_manifest_hash: null,
    outbox_pending_count: 0,
  };
}

/**
 * @param {string} cwd
 * @returns {{state:object, filePath:string}}
 */
export function readState(cwd) {
  const root = findShipRoot(cwd);
  if (!root) throw new Error(".ship/ not found; run 'shipctl config init' first.");
  const filePath = path.join(root, STATE_REL);
  if (!fs.existsSync(filePath)) return { state: defaultState(), filePath };
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
    return { state: { ...defaultState(), ...(parsed || {}) }, filePath };
  } catch (e) {
    throw new Error(`Failed to parse ${filePath}: ${e.message}`);
  }
}

/**
 * @param {string} cwd
 * @param {object} state
 */
export function writeState(cwd, state) {
  const root = findShipRoot(cwd);
  if (!root) throw new Error(".ship/ not found; run 'shipctl config init' first.");
  const filePath = path.join(root, STATE_REL);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, filePath);
  return filePath;
}

export { DEFAULT_CONFIG };
