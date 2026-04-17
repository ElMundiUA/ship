import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const CACHE_REL = path.join(".ship", "cache");
const KIND_ROOTS = ["pattern", "tool", "workflow", "collection", "doc"];

function sanitize(id) {
  return String(id).replace(/\//g, "__");
}

function kindDir(shipRoot, kind) {
  return path.join(shipRoot, CACHE_REL, kind);
}

/**
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} version
 * @param {string} [extension=".md"]
 */
export function cachePath(shipRoot, kind, id, version, extension = ".md") {
  return path.join(kindDir(shipRoot, kind), `${sanitize(id)}@${version}${extension}`);
}

export function metaPath(shipRoot, kind, id, version) {
  return cachePath(shipRoot, kind, id, version, ".meta.json");
}

export function sha256Hex(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * @returns {{content:string, meta:object}|null}
 */
export function readCached(shipRoot, kind, id, version) {
  const body = cachePath(shipRoot, kind, id, version);
  const meta = metaPath(shipRoot, kind, id, version);
  if (!fs.existsSync(body) || !fs.existsSync(meta)) return null;
  try {
    const content = fs.readFileSync(body, "utf8");
    const metaObj = JSON.parse(fs.readFileSync(meta, "utf8"));
    return { content, meta: metaObj };
  } catch {
    return null;
  }
}

/**
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} version
 * @param {string} content
 * @param {object} [meta]
 */
export function writeCached(shipRoot, kind, id, version, content, meta = {}) {
  const body = cachePath(shipRoot, kind, id, version);
  const metaFile = metaPath(shipRoot, kind, id, version);
  fs.mkdirSync(path.dirname(body), { recursive: true });
  fs.writeFileSync(body, content, "utf8");
  const computed = sha256Hex(Buffer.from(content, "utf8"));
  const fullMeta = {
    kind,
    id,
    version,
    content_sha256: meta.content_sha256 || computed,
    updated_at: meta.updated_at || null,
    source_url: meta.source_url || null,
    fetched_at: meta.fetched_at || new Date().toISOString(),
    ...meta,
  };
  fs.writeFileSync(metaFile, `${JSON.stringify(fullMeta, null, 2)}\n`, "utf8");
  return { bodyPath: body, metaPath: metaFile, meta: fullMeta };
}

/**
 * @returns {Array<{kind:string,id:string,version:string,sha256:string,fetched_at:string|null,source_url:string|null}>}
 */
export function listCached(shipRoot) {
  const out = [];
  for (const kind of KIND_ROOTS) {
    const dir = kindDir(shipRoot, kind);
    if (!fs.existsSync(dir)) continue;
    for (const entry of fs.readdirSync(dir)) {
      if (!entry.endsWith(".meta.json")) continue;
      const fullMeta = path.join(dir, entry);
      let metaObj;
      try {
        metaObj = JSON.parse(fs.readFileSync(fullMeta, "utf8"));
      } catch {
        continue;
      }
      out.push({
        kind: metaObj.kind || kind,
        id: metaObj.id,
        version: metaObj.version,
        sha256: metaObj.content_sha256,
        fetched_at: metaObj.fetched_at || null,
        source_url: metaObj.source_url || null,
      });
    }
  }
  return out;
}

export function removeCached(shipRoot, kind, id, version) {
  const body = cachePath(shipRoot, kind, id, version);
  const meta = metaPath(shipRoot, kind, id, version);
  let removed = 0;
  for (const p of [body, meta]) {
    if (fs.existsSync(p)) {
      fs.rmSync(p);
      removed += 1;
    }
  }
  return removed;
}

/**
 * @returns {{ok:boolean, expected:string|null, actual:string|null, reason?:string}}
 */
export function verifyCached(shipRoot, kind, id, version) {
  const body = cachePath(shipRoot, kind, id, version);
  const meta = metaPath(shipRoot, kind, id, version);
  if (!fs.existsSync(body) || !fs.existsSync(meta)) {
    return { ok: false, expected: null, actual: null, reason: "missing body or meta" };
  }
  let metaObj;
  try {
    metaObj = JSON.parse(fs.readFileSync(meta, "utf8"));
  } catch (e) {
    return { ok: false, expected: null, actual: null, reason: `meta parse error: ${e.message}` };
  }
  const expected = metaObj.content_sha256 || null;
  const actual = sha256Hex(fs.readFileSync(body));
  return { ok: expected === actual, expected, actual };
}

/**
 * Returns whether the on-disk cached body still exists and its sha matches the
 * `content_sha256` recorded in the sidecar `.meta.json`. Distinct from
 * `verifyCached` in that the caller needs a specific reason code (missing_body /
 * missing_meta / drift) so `sync` can decide to re-fetch.
 *
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} version
 * @returns {{ok:boolean, reason?:string, expected_sha?:string|null, actual_sha?:string|null}}
 */
export function verifyCachedOnDisk(shipRoot, kind, id, version) {
  const body = cachePath(shipRoot, kind, id, version);
  const meta = metaPath(shipRoot, kind, id, version);
  if (!fs.existsSync(meta)) {
    return { ok: false, reason: "missing_meta" };
  }
  if (!fs.existsSync(body)) {
    return { ok: false, reason: "missing_body" };
  }
  let metaObj;
  try {
    metaObj = JSON.parse(fs.readFileSync(meta, "utf8"));
  } catch (e) {
    return { ok: false, reason: `meta_parse_error: ${e.message}` };
  }
  const expected = metaObj.content_sha256 || null;
  const actual = sha256Hex(fs.readFileSync(body));
  if (expected && actual !== expected) {
    return { ok: false, reason: "drift", expected_sha: expected, actual_sha: actual };
  }
  return { ok: true, expected_sha: expected, actual_sha: actual };
}

/**
 * Minimal YAML front-matter parser. Handles the `---\n<keys>\n---\n` prelude
 * we use for documentation artifacts. Only scalar keys are supported (strings
 * and numbers); quotes and inline arrays are stripped conservatively.
 * @param {string} source
 * @returns {{fm: Record<string, string>, body: string}}
 */
function parseFrontMatter(source) {
  if (typeof source !== "string") return { fm: {}, body: "" };
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(source);
  if (!match) return { fm: {}, body: source };
  const block = match[1];
  const body = source.slice(match[0].length);
  /** @type {Record<string, string>} */
  const fm = {};
  for (const rawLine of block.split(/\r?\n/)) {
    const line = rawLine.replace(/\s+$/, "");
    if (!line || /^\s*#/.test(line)) continue;
    const kv = /^([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(line);
    if (!kv) continue;
    let value = kv[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    fm[kv[1]] = value;
  }
  return { fm, body };
}

/**
 * Read the cached artifact body and parse its YAML front-matter. If
 * `version` is omitted, the highest-version cached entry for (kind,id) is
 * used. Returns `null` when nothing is cached (caller chooses how to
 * degrade).
 *
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} [version]
 * @returns {{fm: Record<string, string>, body: string, version: string, meta: object}|null}
 */
export function readCachedFrontMatter(shipRoot, kind, id, version) {
  let resolvedVersion = version;
  if (!resolvedVersion) {
    const candidates = listCached(shipRoot).filter((e) => e.kind === kind && e.id === id);
    if (!candidates.length) return null;
    candidates.sort((a, b) => String(b.version).localeCompare(String(a.version)));
    resolvedVersion = candidates[0].version;
  }
  const cached = readCached(shipRoot, kind, id, resolvedVersion);
  if (!cached) return null;
  const { fm, body } = parseFrontMatter(cached.content);
  return { fm, body, version: resolvedVersion, meta: cached.meta };
}
