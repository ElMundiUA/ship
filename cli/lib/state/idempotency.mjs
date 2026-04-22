/**
 * Idempotency markers for `kind=once` lanes (RFC-0007).
 *
 * Markers live under `.ship/state/<key>.json` and are expected to be
 * committed to the repo. They record that a particular one-shot lane
 * has already run for a specific version of its pattern. The agent
 * orchestration layer reads them before deciding whether to execute the
 * lane again and writes them on success.
 *
 * File format (version 1):
 *
 *   {
 *     "version": 1,
 *     "lane": "seed_knowledge_starters",
 *     "pattern_id": "onboard-seed-knowledge",
 *     "pattern_sha256": "…",
 *     "pattern_version": "1.0.0",
 *     "completed_at": "2026-04-21T14:20:00Z",
 *     "by": { "run_id": "…", "host": "github-actions" }
 *   }
 *
 * We keep the reader tolerant of extra keys (forward-compat) and the
 * writer strict about the required ones (backward-compat).
 */

import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

import { findShipRoot } from "../config/io.mjs";

export const MARKER_SCHEMA_VERSION = 1;
export const STATE_SUBDIR = path.join(".ship", "state");

const KEY_REGEX = /^[a-z0-9][a-z0-9_.-]{0,127}$/;

/**
 * Resolve the marker path for a given idempotency key. Does not check
 * existence — callers use {@link readMarker} for that.
 *
 * @param {string} cwd
 * @param {string} key
 * @returns {{ root: string, markerPath: string }}
 */
export function resolveMarkerPath(cwd, key) {
  if (typeof key !== "string" || !KEY_REGEX.test(key)) {
    throw new Error(
      `idempotency key must match /^[a-z0-9][a-z0-9_.-]{0,127}$/; got ${JSON.stringify(key)}`,
    );
  }
  const root = findShipRoot(cwd);
  if (!root) {
    throw new Error(
      ".ship/config.yml not found; idempotency markers require a Ship-adopted repo",
    );
  }
  return {
    root,
    markerPath: path.join(root, STATE_SUBDIR, `${key}.json`),
  };
}

/**
 * Read and validate an idempotency marker. Returns `null` when the
 * marker file doesn't exist; throws on malformed JSON or schema
 * mismatch (callers should decide whether to treat that as fatal —
 * `shipctl run` currently treats it as fatal so a broken marker can't
 * silently cause a re-seed).
 *
 * @param {string} cwd
 * @param {string} key
 * @returns {object | null}
 */
export function readMarker(cwd, key) {
  const { markerPath } = resolveMarkerPath(cwd, key);
  if (!fs.existsSync(markerPath)) return null;
  let raw;
  try {
    raw = fs.readFileSync(markerPath, "utf8");
  } catch (e) {
    throw new Error(`idempotency: failed to read ${markerPath}: ${e.message}`);
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    throw new Error(`idempotency: ${markerPath} is not valid JSON: ${e.message}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`idempotency: ${markerPath} must be a JSON object`);
  }
  if (parsed.version !== MARKER_SCHEMA_VERSION) {
    throw new Error(
      `idempotency: ${markerPath} has marker version ${JSON.stringify(parsed.version)}; expected ${MARKER_SCHEMA_VERSION}`,
    );
  }
  return parsed;
}

/**
 * Write an idempotency marker atomically (tmp file + rename). Creates
 * `.ship/state/` if missing.
 *
 * @param {string} cwd
 * @param {string} key
 * @param {{
 *   lane: string,
 *   pattern_id: string,
 *   pattern_sha256: string,
 *   pattern_version?: string | null,
 *   by?: Record<string, unknown>,
 *   completed_at?: string,
 * }} fields
 * @returns {{ markerPath: string, marker: object }}
 */
export function writeMarker(cwd, key, fields) {
  const { markerPath } = resolveMarkerPath(cwd, key);
  const marker = {
    version: MARKER_SCHEMA_VERSION,
    lane: String(fields.lane),
    pattern_id: String(fields.pattern_id),
    pattern_sha256: String(fields.pattern_sha256),
    pattern_version: fields.pattern_version ?? null,
    completed_at: fields.completed_at ?? new Date().toISOString(),
    by: fields.by ?? defaultActorContext(),
  };

  const required = ["lane", "pattern_id", "pattern_sha256"];
  for (const k of required) {
    if (!marker[k]) throw new Error(`idempotency: missing required field '${k}'`);
  }

  fs.mkdirSync(path.dirname(markerPath), { recursive: true });
  const body = `${JSON.stringify(marker, null, 2)}\n`;
  const tmp = `${markerPath}.tmp`;
  fs.writeFileSync(tmp, body, "utf8");
  fs.renameSync(tmp, markerPath);
  return { markerPath, marker };
}

/**
 * Decide whether a `kind=once` lane should run given its current
 * marker and the current pattern body.
 *
 * @param {object | null} marker      result of readMarker()
 * @param {string} patternBody        full ARTIFACT.md text (including frontmatter)
 * @param {"version-change" | "manual"} resetOn
 * @returns {{ run: true, reason: "no-marker" | "sha-changed" } | { run: false, reason: "already-done", marker: object }}
 */
export function decideRun(marker, patternBody, resetOn) {
  if (!marker) return { run: true, reason: "no-marker" };
  const sha = sha256(patternBody);
  if (resetOn === "version-change" && marker.pattern_sha256 !== sha) {
    return { run: true, reason: "sha-changed" };
  }
  return { run: false, reason: "already-done", marker };
}

/**
 * SHA-256 of the full pattern body (frontmatter included). Exposed so
 * callers can recompute it when writing markers without re-parsing.
 *
 * @param {string} text
 * @returns {string}
 */
export function sha256(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function defaultActorContext() {
  /* Prefer the richest available CI context but keep it small — this
   * blob ends up committed in the repo, so we don't want noise. */
  const ctx = {};
  if (process.env.GITHUB_ACTIONS) ctx.host = "github-actions";
  else if (process.env.GITLAB_CI) ctx.host = "gitlab-ci";
  else if (process.env.CI) ctx.host = "ci";
  else ctx.host = "local";

  if (process.env.SHIP_RUN_ID) ctx.run_id = process.env.SHIP_RUN_ID;
  else if (process.env.GITHUB_RUN_ID) ctx.run_id = process.env.GITHUB_RUN_ID;

  return ctx;
}
