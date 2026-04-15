import fs from "node:fs";
import path from "node:path";
import { apiGet } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";

const MANIFEST_REL = "patterns/manifest.json";

/**
 * @param {Record<string, unknown>} data
 */
function parseManifest(data) {
  const patterns = /** @type {unknown} */ (data.patterns);
  if (!Array.isArray(patterns)) {
    throw new Error(`${MANIFEST_REL} must contain a "patterns" array.`);
  }
  return {
    version: data.version ?? 1,
    description: typeof data.description === "string" ? data.description : "",
    patterns: /** @type {Array<Record<string, unknown>>} */ (patterns),
  };
}

/**
 * @param {Record<string, unknown>} p
 */
function slimEntry(p) {
  return {
    id: p.id,
    title: p.title,
    summary: p.summary,
    path: p.path,
    tags: Array.isArray(p.tags) ? p.tags : [],
    group: p.group,
  };
}

/**
 * @param {string} root
 * @param {{ json: boolean }} ctx
 * @param {string} sub
 * @param {string[]} rest
 */
async function patternsFromDisk(root, ctx, sub, rest) {
  const manifestPath = path.join(root, MANIFEST_REL);
  const raw = fs.readFileSync(manifestPath, "utf8");
  /** @type {Record<string, unknown>} */
  const manifest = JSON.parse(raw);
  const { version, description, patterns } = parseManifest(manifest);
  const entries = patterns.filter((p) => p && typeof p === "object" && typeof p.id === "string");

  if (sub === "list") {
    const slim = entries.map((p) => slimEntry(p));
    const out = { version, description, patterns: slim };
    if (ctx.json) console.log(JSON.stringify(out, null, 2));
    else {
      console.log(`${description || "Patterns"}\n`);
      for (const p of slim) {
        console.log(`- ${p.id}`);
        console.log(`  ${p.title}`);
        const tags = (p.tags || []).join(", ");
        console.log(`  path: ${p.path}  tags: ${tags}\n`);
      }
    }
    return;
  }

  if (sub === "show") {
    const id = rest[0];
    if (!id) {
      console.error("show: pattern id required.");
      process.exit(1);
    }
    const entry = entries.find((e) => e.id === id);
    if (!entry) {
      console.error(`Unknown id: ${id}`);
      process.exit(1);
    }
    const rel = entry.path;
    if (typeof rel !== "string" || !rel.trim()) {
      console.error("Pattern entry has no path.");
      process.exit(1);
    }
    const abs = path.resolve(root, rel);
    const rootNorm = root.endsWith(path.sep) ? root.slice(0, -1) : root;
    const absNorm = abs.endsWith(path.sep) ? abs.slice(0, -1) : abs;
    if (absNorm !== rootNorm && !abs.startsWith(root + path.sep)) {
      console.error("Manifest path escapes repository root.");
      process.exit(1);
    }
    if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
      console.error(`Missing file: ${rel}`);
      process.exit(1);
    }
    const content = fs.readFileSync(abs, "utf8");
    const full = { ...slimEntry(entry), content };
    if (ctx.json) console.log(JSON.stringify(full, null, 2));
    else {
      console.log(`# ${entry.title} (${entry.id})\n`);
      console.log(content);
    }
    return;
  }

  console.error(`Unknown patterns subcommand: ${sub}`);
  process.exit(1);
}

/**
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string} sub
 * @param {string[]} rest
 */
async function patternsFromHosted(ctx, sub, rest) {
  const base = ctx.baseUrl;
  if (sub === "list") {
    const data = await apiGet(base, "/patterns");
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`${data.description || "Patterns"}\n`);
      for (const p of data.patterns || []) {
        console.log(`- ${p.id}`);
        console.log(`  ${p.title}`);
        console.log(`  path: ${p.path}  tags: ${(p.tags || []).join(", ")}\n`);
      }
    }
    return;
  }
  if (sub === "show") {
    const id = rest[0];
    if (!id) {
      console.error("show: pattern id required.");
      process.exit(1);
    }
    const data = await apiGet(base, `/patterns/${encodeURIComponent(id)}`);
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`# ${data.title} (${data.id})\n`);
      console.log(data.content);
    }
    return;
  }
  console.error(`Unknown patterns subcommand: ${sub}`);
  process.exit(1);
}

/**
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function patternsCommand(ctx, args) {
  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  ship patterns list
  ship patterns show <pattern-id>

With a local Ship tree (cwd or SHIP_REPO): reads patterns/manifest.json on disk.
Otherwise: same HTTP API as methodology — GET /patterns and GET /patterns/{id} (SHIP_API_BASE / --base-url).

Global flags: --base-url URL  --json`);
    return;
  }

  const root = resolveShipRepoRootForCatalog();
  if (root) {
    await patternsFromDisk(root, ctx, sub, rest);
  } else {
    await patternsFromHosted(ctx, sub, rest);
  }
}
