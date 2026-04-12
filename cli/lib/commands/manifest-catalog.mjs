import fs from "node:fs";
import path from "node:path";
import { findShipRepoRoot } from "../find-ship-root.mjs";

/** @type {Record<string, { manifestRel: string; arrayKey: string; name: string }>} */
const CATALOGS = {
  tools: { manifestRel: "tools/manifest.json", arrayKey: "tools", name: "Tools" },
  workflows: { manifestRel: "workflows/manifest.json", arrayKey: "workflows", name: "Workflows" },
  collections: { manifestRel: "collections/manifest.json", arrayKey: "collections", name: "Collections" },
};

/**
 * @param {"tools"|"workflows"|"collections"} kind
 * @param {{ json: boolean }} ctx
 * @param {string[]} args subcommand tail (e.g. `list` or `show`, `linear`)
 */
export async function manifestCatalogCommand(kind, ctx, args) {
  const spec = CATALOGS[kind];
  if (!spec) throw new Error(`Unknown catalog kind: ${kind}`);

  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  ship ${kind} list
  ship ${kind} show <id>

Run from the Ship repository, or set SHIP_REPO to its root. Same manifest the landing site reads.

Global flags: --json`);
    return;
  }

  const root = findShipRepoRoot();
  const manifestPath = path.join(root, spec.manifestRel);
  const raw = fs.readFileSync(manifestPath, "utf8");
  /** @type {Record<string, unknown>} */
  const data = JSON.parse(raw);
  const entries = /** @type {Array<{ id: string; title: string; summary?: string; path: string; tags?: string[] }>} */ (
    data[spec.arrayKey] || []
  );

  if (sub === "list") {
    if (ctx.json) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      console.log(`${data.description || spec.name}\n`);
      for (const p of entries) {
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
      console.error("show: id required.");
      process.exit(1);
    }
    const entry = entries.find((e) => e.id === id);
    if (!entry) {
      console.error(`Unknown id: ${id}`);
      process.exit(1);
    }
    const abs = path.resolve(root, entry.path);
    const rootNorm = root.endsWith(path.sep) ? root.slice(0, -1) : root;
    const absNorm = abs.endsWith(path.sep) ? abs.slice(0, -1) : abs;
    if (absNorm !== rootNorm && !abs.startsWith(root + path.sep)) {
      console.error("Manifest path escapes repository root.");
      process.exit(1);
    }
    if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
      console.error(`Missing file: ${entry.path}`);
      process.exit(1);
    }
    const content = fs.readFileSync(abs, "utf8");
    if (ctx.json) {
      console.log(JSON.stringify({ ...entry, content }, null, 2));
    } else {
      console.log(`# ${entry.title} (${entry.id})\n`);
      console.log(content);
    }
    return;
  }

  console.error(`Unknown ${kind} subcommand: ${sub}`);
  process.exit(1);
}
