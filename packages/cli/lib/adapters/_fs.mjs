import fs from "node:fs";
import path from "node:path";

/**
 * Shared filesystem helpers for adapter `detect()` hooks.
 *
 * All helpers are synchronous (detect() is still exposed as async so future
 * adapters that need I/O concurrency can use it freely) and **never throw** on
 * missing files — adapters must return `present:false` instead of propagating
 * ENOENT up to the doctor command.
 */

export function exists(cwd, ...rel) {
  try {
    return fs.existsSync(path.join(cwd, ...rel));
  } catch {
    return false;
  }
}

export function isFile(cwd, ...rel) {
  try {
    const s = fs.statSync(path.join(cwd, ...rel));
    return s.isFile();
  } catch {
    return false;
  }
}

export function isDir(cwd, ...rel) {
  try {
    const s = fs.statSync(path.join(cwd, ...rel));
    return s.isDirectory();
  } catch {
    return false;
  }
}

export function readText(cwd, rel) {
  try {
    return fs.readFileSync(path.join(cwd, rel), "utf8");
  } catch {
    return null;
  }
}

export function readJson(cwd, rel) {
  const txt = readText(cwd, rel);
  if (txt == null) return null;
  try {
    return JSON.parse(txt);
  } catch {
    return null;
  }
}

/**
 * List direct children of `cwd/rel`. Returns [] if missing / not a directory.
 */
export function listDir(cwd, rel) {
  try {
    return fs.readdirSync(path.join(cwd, rel));
  } catch {
    return [];
  }
}

/**
 * Shallow recursive walk capped at depth and file count to avoid hammering
 * huge monorepos during `shipctl doctor`.
 */
export function walk(cwd, { maxDepth = 3, maxFiles = 500, ignore = DEFAULT_IGNORE } = {}) {
  const out = [];
  const base = path.resolve(cwd);
  function visit(dir, depth) {
    if (out.length >= maxFiles) return;
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (out.length >= maxFiles) return;
      if (ignore.has(e.name)) continue;
      const full = path.join(dir, e.name);
      const rel = path.relative(base, full);
      if (e.isDirectory()) {
        if (depth < maxDepth) visit(full, depth + 1);
      } else if (e.isFile()) {
        out.push(rel);
      }
    }
  }
  visit(base, 0);
  return out;
}

export const DEFAULT_IGNORE = new Set([
  "node_modules",
  ".git",
  "dist",
  "build",
  ".next",
  "out",
  "coverage",
  ".turbo",
  "target",
  "venv",
  ".venv",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".gradle",
  "Pods",
  "DerivedData",
]);

/**
 * Read every .env*-style file at the repo root. Returns an array of
 * `{file, content}`. Hidden/ignored by .gitignore is not a concern — we are
 * only scanning for variable *names*, never values.
 */
export function readEnvFiles(cwd) {
  const hits = [];
  const entries = listDir(cwd, ".");
  for (const name of entries) {
    if (!/^\.env($|[.\-])/.test(name)) continue;
    if (!isFile(cwd, name)) continue;
    const content = readText(cwd, name);
    if (content != null) hits.push({ file: name, content });
  }
  return hits;
}

/**
 * Read every YAML workflow under `.github/workflows/`. Returns
 * `[{file, content}]`.
 */
export function readGithubWorkflows(cwd) {
  const dir = path.join(".github", "workflows");
  if (!isDir(cwd, dir)) return [];
  const hits = [];
  for (const name of listDir(cwd, dir)) {
    if (!/\.ya?ml$/i.test(name)) continue;
    const rel = path.join(dir, name);
    const content = readText(cwd, rel);
    if (content != null) hits.push({ file: rel, content });
  }
  return hits;
}

/**
 * Flatten a package.json's dependency sections into a single `{name → range}`
 * map for easier inspection.
 */
export function pkgDeps(pkg) {
  if (!pkg || typeof pkg !== "object") return {};
  const all = {};
  for (const key of ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]) {
    const obj = pkg[key];
    if (obj && typeof obj === "object") Object.assign(all, obj);
  }
  return all;
}
