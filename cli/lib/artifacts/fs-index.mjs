import fs from "node:fs";
import path from "node:path";

/**
 * Shared filesystem index for v2 artifact trees: walks
 * `<repoRoot>/artifacts/<plural>/<id>/ARTIFACT.md` and parses just enough YAML
 * front-matter to reconstruct the same entry shape we used to read out of the
 * legacy `<plural>/manifest.json` files.
 *
 * Zero dependencies — only node builtins. Anything we cannot parse falls
 * through (the entry still gets emitted with whatever fields we recovered).
 */

const KIND_TO_PLURAL = {
  pattern: "patterns",
  tool: "tools",
  collection: "collections",
};

/**
 * @param {"pattern"|"tool"|"collection"} kind
 */
export function pluralFor(kind) {
  return KIND_TO_PLURAL[kind] || `${kind}s`;
}

/**
 * Walk `artifacts/<plural>/*` and return the parsed entries (same shape as the
 * legacy manifest).
 *
 * @param {string} repoRoot
 * @param {"pattern"|"tool"|"collection"} kind
 * @returns {Array<Record<string, any>>}
 */
export function scanArtifacts(repoRoot, kind) {
  const plural = pluralFor(kind);
  const dir = path.join(repoRoot, "artifacts", plural);
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return [];

  const ids = fs.readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();

  /** @type {Array<Record<string, any>>} */
  const out = [];
  for (const id of ids) {
    const file = path.join(dir, id, "ARTIFACT.md");
    if (!fs.existsSync(file)) continue;
    let raw;
    try {
      raw = fs.readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const { fm } = parseFrontMatter(raw);
    const entry = entryFromFrontmatter(fm, kind, id);
    out.push(entry);
  }
  return out;
}

/**
 * Read the full ARTIFACT.md (frontmatter + body) for a specific id. Returns
 * null when the file is absent so callers can emit the same "Unknown id"
 * messages they did before.
 *
 * @param {string} repoRoot
 * @param {"pattern"|"tool"|"collection"} kind
 * @param {string} id
 */
export function readArtifactFile(repoRoot, kind, id) {
  const plural = pluralFor(kind);
  const file = path.join(repoRoot, "artifacts", plural, id, "ARTIFACT.md");
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return null;
  return { absPath: file, content: fs.readFileSync(file, "utf8") };
}

function entryFromFrontmatter(fm, kind, id) {
  const plural = pluralFor(kind);
  const description = typeof fm.description === "string" ? fm.description : "";
  const summary = description ? firstSentence(description) : "";
  return {
    id: typeof fm.id === "string" && fm.id ? fm.id : id,
    title: typeof fm.name === "string" ? fm.name : id,
    summary,
    path: `artifacts/${plural}/${id}/ARTIFACT.md`,
    tags: Array.isArray(fm.tags) ? fm.tags : [],
    group: typeof fm.group === "string" ? fm.group : null,
    version: typeof fm.version === "string" ? fm.version : null,
    content_sha256: typeof fm.content_sha256 === "string" ? fm.content_sha256 : null,
    updated_at: typeof fm.updated_at === "string" ? fm.updated_at : null,
    channel: typeof fm.channel === "string" ? fm.channel : null,
    min_shipctl: typeof fm.min_shipctl === "string" ? fm.min_shipctl : null,
    deprecated: fm.deprecated === true || fm.deprecated === "true",
    replaced_by: fm.replaced_by ?? null,
    yanked: fm.yanked === true || fm.yanked === "true",
  };
}

function firstSentence(text) {
  const trimmed = text.trim();
  if (!trimmed) return "";
  const m = /[.!?](\s|$)/.exec(trimmed);
  if (!m) return trimmed;
  return trimmed.slice(0, m.index + 1).trim();
}

/**
 * Tiny YAML front-matter parser tailored for v2 ARTIFACT.md files.
 *
 * Supports:
 *   - simple `key: value`
 *   - inline lists `key: [a, b]`
 *   - folded scalars `key: >` / `key: >-` with indented continuation lines
 *   - quoted strings (single or double)
 *   - one level of nested mapping (used by `spec:`)
 *   - comments (`# …`)
 *
 * Anything else is best-effort: the value is captured as the trimmed string.
 *
 * @param {string} source
 * @returns {{fm: Record<string, any>, body: string}}
 */
export function parseFrontMatter(source) {
  if (typeof source !== "string") return { fm: {}, body: "" };
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(source);
  if (!match) return { fm: {}, body: source };
  const block = match[1];
  const body = source.slice(match[0].length);
  /** @type {Record<string, any>} */
  const fm = {};
  const lines = block.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const rawLine = lines[i];
    const line = rawLine.replace(/\s+$/, "");
    if (!line || /^\s*#/.test(line)) {
      i += 1;
      continue;
    }
    const top = /^([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(line);
    if (!top) {
      i += 1;
      continue;
    }
    const key = top[1];
    const value = top[2];

    if (value === ">" || value === ">-") {
      const folded = [];
      i += 1;
      while (i < lines.length) {
        const cont = lines[i];
        if (cont === "" || cont === "\r") {
          // Preserve paragraph breaks as a single space in folded scalars.
          folded.push("");
          i += 1;
          continue;
        }
        const m = /^(\s+)(.*)$/.exec(cont);
        if (!m) break;
        folded.push(m[2]);
        i += 1;
      }
      let joined = folded.join(" ").replace(/\s+/g, " ").trim();
      if (value === ">-") joined = joined.replace(/\s+$/, "");
      fm[key] = joined;
      continue;
    }

    if (/^\[.*\]$/.test(value.trim())) {
      const inner = value.trim().slice(1, -1).trim();
      fm[key] = inner.length ? inner.split(/\s*,\s*/).map(unquote) : [];
      i += 1;
      continue;
    }

    if (value === "") {
      // Possible nested mapping or empty scalar. Peek ahead.
      const child = {};
      let saw = false;
      let j = i + 1;
      while (j < lines.length) {
        const cont = lines[j];
        if (!cont.trim()) { j += 1; continue; }
        const indented = /^(\s{2,})([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(cont);
        if (!indented) break;
        const [, , subKey, subVal] = indented;
        if (/^\[.*\]$/.test(subVal.trim())) {
          const inner = subVal.trim().slice(1, -1).trim();
          child[subKey] = inner.length ? inner.split(/\s*,\s*/).map(unquote) : [];
        } else {
          child[subKey] = coerceScalar(subVal);
        }
        saw = true;
        j += 1;
      }
      if (saw) {
        fm[key] = child;
        i = j;
        continue;
      }
      fm[key] = "";
      i += 1;
      continue;
    }

    fm[key] = coerceScalar(value);
    i += 1;
  }
  return { fm, body };
}

function unquote(value) {
  if (typeof value !== "string") return value;
  const v = value.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  return v;
}

function coerceScalar(rawValue) {
  const v = unquote(String(rawValue).trim());
  if (v === "true") return true;
  if (v === "false") return false;
  if (v === "null" || v === "~") return null;
  return v;
}
