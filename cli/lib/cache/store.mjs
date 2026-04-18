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
 * Folder that holds the artifact body + sidecar meta. New v2 cache layout:
 *   .ship/cache/<kind>/<sanitize(id)>@<version>/ARTIFACT.md
 *   .ship/cache/<kind>/<sanitize(id)>@<version>/.meta.json
 */
export function cacheFolder(shipRoot, kind, id, version) {
  return path.join(kindDir(shipRoot, kind), `${sanitize(id)}@${version}`);
}

/**
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} version
 * @param {string} [extension]   Reserved for back-compat; ignored under the
 *                               new folder layout (always returns ARTIFACT.md).
 */
export function cachePath(shipRoot, kind, id, version, extension = ".md") {
  // Keep the trailing-extension parameter so callers that opted into the old
  // ".meta.json" trick still work (they used to call `cachePath(..., ".meta.json")`
  // — those callers should now use `metaPath` instead, but stay defensive).
  if (extension === ".meta.json") {
    return path.join(cacheFolder(shipRoot, kind, id, version), ".meta.json");
  }
  return path.join(cacheFolder(shipRoot, kind, id, version), "ARTIFACT.md");
}

export function metaPath(shipRoot, kind, id, version) {
  return path.join(cacheFolder(shipRoot, kind, id, version), ".meta.json");
}

export function sha256Hex(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * RFC-0005 hashing convention: `content_sha256` is computed over the
 * artifact bytes with the `content_sha256:` value cleared (the line stays,
 * but the hex value is replaced by empty). This avoids the chicken-and-egg
 * of hashing a file whose own hash lives inside it. Both the server-side
 * stamp and any client-side verification must apply the same normalization.
 *
 * @param {string} content
 * @returns {string}
 */
export function normalizeForArtifactSha(content) {
  return String(content).replace(
    /^(content_sha256:\s*)[A-Fa-f0-9]+\s*$/m,
    "$1",
  );
}

/**
 * Hash an artifact body the way the server does — with the sha line cleared.
 *
 * @param {string|Buffer} content
 * @returns {string}
 */
export function artifactSha256(content) {
  const text = Buffer.isBuffer(content) ? content.toString("utf8") : String(content);
  return sha256Hex(Buffer.from(normalizeForArtifactSha(text), "utf8"));
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
  const folder = cacheFolder(shipRoot, kind, id, version);
  const body = cachePath(shipRoot, kind, id, version);
  const metaFile = metaPath(shipRoot, kind, id, version);
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(body, content, "utf8");
  // Trust the server-stamped sha256 when present; otherwise hash the body
  // we just wrote using the RFC-0005 normalized form (sha line cleared) so
  // future verifies match. The artifact folder currently holds only
  // ARTIFACT.md + .meta.json, so a body-only hash is equivalent to a
  // folder-walk hash once .meta.json is excluded.
  const computed = artifactSha256(content);
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
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const fullMeta = path.join(dir, entry.name, ".meta.json");
      if (!fs.existsSync(fullMeta)) continue;
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
  const folder = cacheFolder(shipRoot, kind, id, version);
  if (!fs.existsSync(folder)) return 0;
  // Count what we are about to remove so callers (and tests) can assert how
  // many "things" disappeared. Historically removeCached returned 2 (body +
  // meta), so cap the count to match for the common case.
  let removed = 0;
  for (const f of ["ARTIFACT.md", ".meta.json"]) {
    if (fs.existsSync(path.join(folder, f))) removed += 1;
  }
  fs.rmSync(folder, { recursive: true, force: true });
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
  const actual = artifactSha256(fs.readFileSync(body));
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
  const actual = artifactSha256(fs.readFileSync(body));
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
 * Slightly richer parser used by `readCachedArtifact`. Recognises the v2
 * `spec:` block (one level of nested `key: value` indented by 2 spaces) and
 * inline list / quoted scalar conventions. Anything we cannot parse falls
 * through with a best-effort string value (callers degrade gracefully).
 *
 * @param {string} source
 * @returns {{fm: Record<string, any>, body: string, spec: Record<string, any>}}
 */
function parseFrontMatterV2(source) {
  if (typeof source !== "string") return { fm: {}, body: "", spec: {} };
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(source);
  if (!match) return { fm: {}, body: source, spec: {} };
  const block = match[1];
  const body = source.slice(match[0].length);
  /** @type {Record<string, any>} */
  const fm = {};
  /** @type {Record<string, any>} */
  const spec = {};

  const lines = block.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const rawLine = lines[i];
    const line = rawLine.replace(/\s+$/, "");
    if (!line || /^\s*#/.test(line)) {
      i += 1;
      continue;
    }

    // Top-level key: scalar | list | folded.
    const top = /^([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(line);
    if (!top) {
      i += 1;
      continue;
    }
    const key = top[1];
    let value = top[2];

    // Folded scalars: `>` or `>-` then indented continuation lines.
    if (value === ">" || value === ">-") {
      const folded = [];
      i += 1;
      while (i < lines.length) {
        const cont = lines[i];
        const m = /^(\s+)(.*)$/.exec(cont);
        if (!m) break;
        folded.push(m[2]);
        i += 1;
      }
      fm[key] = folded.join(" ").trim();
      continue;
    }

    // Inline list: `[a, b, c]`.
    if (/^\[.*\]$/.test(value.trim())) {
      const inner = value.trim().slice(1, -1).trim();
      fm[key] = inner.length
        ? inner.split(/\s*,\s*/).map((v) => unquote(v))
        : [];
      i += 1;
      continue;
    }

    // Nested block (currently only `spec:` is recognised).
    if (value === "" && key === "spec") {
      i += 1;
      while (i < lines.length) {
        const cont = lines[i];
        if (!cont.trim()) {
          i += 1;
          continue;
        }
        const indented = /^(\s{2,})([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(cont);
        if (!indented) break;
        const [, , subKey, subVal] = indented;
        if (/^\[.*\]$/.test(subVal.trim())) {
          const inner = subVal.trim().slice(1, -1).trim();
          spec[subKey] = inner.length
            ? inner.split(/\s*,\s*/).map((v) => unquote(v))
            : [];
        } else {
          spec[subKey] = unquote(subVal.trim());
        }
        i += 1;
      }
      fm.spec = spec;
      continue;
    }

    fm[key] = unquote(value.trim());
    i += 1;
  }

  return { fm, body, spec };
}

function unquote(value) {
  if (typeof value !== "string") return value;
  const v = value.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  return v;
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

/**
 * v2-aware variant of `readCachedFrontMatter`. Uses the richer parser so
 * callers can read `spec.install_target` (or other nested keys) without
 * pulling in a YAML dep.
 *
 * @param {string} shipRoot
 * @param {string} kind
 * @param {string} id
 * @param {string} [version]
 * @returns {{fm:Record<string,any>, body:string, version:string, meta:object, spec:Record<string,any>}|null}
 */
export function readCachedArtifact(shipRoot, kind, id, version) {
  let resolvedVersion = version;
  if (!resolvedVersion) {
    const candidates = listCached(shipRoot).filter((e) => e.kind === kind && e.id === id);
    if (!candidates.length) return null;
    candidates.sort((a, b) => String(b.version).localeCompare(String(a.version)));
    resolvedVersion = candidates[0].version;
  }
  const cached = readCached(shipRoot, kind, id, resolvedVersion);
  if (!cached) return null;
  const { fm, body, spec } = parseFrontMatterV2(cached.content);
  return { fm, body, version: resolvedVersion, meta: cached.meta, spec };
}
