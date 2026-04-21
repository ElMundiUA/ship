/**
 * `shipctl.lock.json` — reproducible-build anchor for Ship lanes (RFC-0007
 * Phase 4). Records the exact `(kind, id, version, content_sha256)` of
 * every artifact a lane depends on, plus where that body is materialised on
 * disk. Without it, `shipctl run --offline` has no way to decide whether
 * a cached pattern is "the one the lane expects" — the lockfile gives a
 * positive, auditable answer.
 *
 * Only patterns are locked today (lanes only reference patterns via
 * `lane.pattern`). The schema is forward-compatible: tools and collections
 * can be added to the same `artifacts` map with no version bump.
 *
 * Concurrency: the writer does an atomic rename (write to `.tmp` in the
 * same directory, rename over the target). Readers always open a read-only
 * handle so a race doesn't yield a truncated parse.
 */

import fs from "node:fs";
import path from "node:path";

import { artifactSha256 } from "../cache/store.mjs";

const LOCKFILE_REL = path.join(".ship", "shipctl.lock.json");
export const LOCKFILE_SCHEMA_VERSION = 1;

export function lockfilePath(shipRoot) {
  return path.join(shipRoot, LOCKFILE_REL);
}

/**
 * @typedef {Object} LockfileEntry
 * @property {string} version             Resolved version of the artifact.
 * @property {string} content_sha256      Hex digest (RFC-0005 normalisation).
 * @property {string} cached_path         Relative path inside the ship root.
 * @property {"http" | "monorepo" | "inline"} source
 * @property {boolean} pinned             Whether a config pin constrained this.
 * @property {string} [channel]           Manifest channel at time of lock.
 *
 * @typedef {Object} Lockfile
 * @property {1} version
 * @property {string} generated_at        ISO-8601 UTC.
 * @property {string} shipctl_version
 * @property {{ base_url: string, channel: string } | null} source
 * @property {Record<string, LockfileEntry>} artifacts  Key: "<kind>/<id>".
 * @property {string[]} [notes]           Free-form operator hints.
 */

/**
 * @param {string} shipRoot
 * @returns {Lockfile | null}
 */
export function readLockfile(shipRoot) {
  const file = lockfilePath(shipRoot);
  if (!fs.existsSync(file)) return null;
  const raw = fs.readFileSync(file, "utf8");
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`shipctl.lock.json: invalid JSON (${err instanceof Error ? err.message : err})`);
  }
  if (!parsed || typeof parsed !== "object") {
    throw new Error("shipctl.lock.json: root must be an object");
  }
  if (parsed.version !== LOCKFILE_SCHEMA_VERSION) {
    throw new Error(
      `shipctl.lock.json: unsupported version ${parsed.version} (shipctl supports v${LOCKFILE_SCHEMA_VERSION}).`,
    );
  }
  if (!parsed.artifacts || typeof parsed.artifacts !== "object") {
    throw new Error("shipctl.lock.json: missing `artifacts` map");
  }
  return parsed;
}

/**
 * @param {string} shipRoot
 * @param {Lockfile} data
 */
export function writeLockfile(shipRoot, data) {
  const file = lockfilePath(shipRoot);
  fs.mkdirSync(path.dirname(file), { recursive: true });

  /* Sort the artifacts map so diffs stay minimal between runs. We can't
   * rely on JSON object key order, but `JSON.stringify` honours insertion
   * order on v8, and every modern engine we care about follows suit. */
  const sorted = {};
  const keys = Object.keys(data.artifacts || {}).sort();
  for (const k of keys) sorted[k] = data.artifacts[k];

  const normalised = {
    version: LOCKFILE_SCHEMA_VERSION,
    generated_at: data.generated_at,
    shipctl_version: data.shipctl_version,
    source: data.source || null,
    artifacts: sorted,
    notes: Array.isArray(data.notes) ? [...data.notes] : [],
  };

  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(normalised, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, file);
}

/**
 * Build the lookup key the rest of shipctl uses.
 * @param {string} kind
 * @param {string} id
 */
export function lockKey(kind, id) {
  return `${kind}/${id}`;
}

/**
 * @param {Lockfile} lock
 * @param {string} kind
 * @param {string} id
 * @returns {LockfileEntry | null}
 */
export function lookupLock(lock, kind, id) {
  if (!lock) return null;
  const key = lockKey(kind, id);
  const entry = lock.artifacts?.[key];
  return entry || null;
}

/**
 * Compute the lockfile entry for a freshly-materialised artifact body.
 * Keeping the signature narrow — just `body` and a bit of provenance —
 * means the caller can't accidentally smuggle transient fields into the
 * lock.
 *
 * @param {Object} params
 * @param {string} params.body           Artifact text (markdown + frontmatter).
 * @param {string} params.version        Resolved version string.
 * @param {string} params.cachedPath     Path relative to the ship root.
 * @param {"http" | "monorepo" | "inline"} params.source
 * @param {boolean} [params.pinned=false]
 * @param {string} [params.channel]
 * @returns {LockfileEntry}
 */
export function entryFromBody({
  body,
  version,
  cachedPath,
  source,
  pinned = false,
  channel,
}) {
  return {
    version,
    content_sha256: artifactSha256(body),
    cached_path: String(cachedPath).replace(/\\/g, "/"),
    source,
    pinned: Boolean(pinned),
    ...(channel ? { channel: String(channel) } : {}),
  };
}

/**
 * Decide whether a body matches a lockfile entry. Returns a structured
 * `{ ok, reason }` so callers can emit specific diagnostics.
 *
 * @param {LockfileEntry | null} entry
 * @param {string} body
 * @returns {{ ok: boolean, reason?: string, expected?: string, actual?: string }}
 */
export function verifyBody(entry, body) {
  if (!entry) return { ok: false, reason: "missing-entry" };
  const actual = artifactSha256(body);
  if (actual !== entry.content_sha256) {
    return {
      ok: false,
      reason: "sha-mismatch",
      expected: entry.content_sha256,
      actual,
    };
  }
  return { ok: true };
}
