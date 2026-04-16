import fs from "node:fs";
import path from "node:path";
import { apiGet, apiPost } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { searchCommand } from "./search.mjs";

/** @type {Record<string, { manifestRel: string; arrayKey: string; name: string; apiPath: string; fetchKind: string }>} */
const RESOURCES = {
  tool: {
    manifestRel: "tools/manifest.json",
    arrayKey: "tools",
    name: "Tools",
    apiPath: "tools",
    fetchKind: "tool",
  },
  workflow: {
    manifestRel: "workflows/manifest.json",
    arrayKey: "workflows",
    name: "Workflows",
    apiPath: "workflows",
    fetchKind: "workflow",
  },
  collection: {
    manifestRel: "collections/manifest.json",
    arrayKey: "collections",
    name: "Collections",
    apiPath: "collections",
    fetchKind: "collection",
  },
};

/**
 * @param {"tool"|"workflow"|"collection"} resource
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function resourceManifestCommand(resource, ctx, args) {
  const spec = RESOURCES[resource];
  if (!spec) throw new Error(`Unknown resource: ${resource}`);

  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  ship ${resource} list
  ship ${resource} show <id>
  ship ${resource} fetch <id>
  ship ${resource} search <query> [--top-k N]

With a local Ship tree (cwd or SHIP_REPO): reads ${spec.manifestRel} on disk.
Otherwise: methodology API (GET /${spec.apiPath}, POST /fetch for fetch, POST /search for search).

Plural alias: ship ${spec.apiPath} …

Global flags: --base-url URL  --json`);
    return;
  }

  if (sub === "search") {
    await searchCommand(ctx, rest);
    return;
  }

  const root = resolveShipRepoRootForCatalog();
  if (root) {
    await manifestFromDisk(resource, root, spec, ctx, sub, rest);
  } else {
    await manifestFromHosted(resource, spec, ctx, sub, rest);
  }
}

/**
 * @param {"tool"|"workflow"|"collection"} resource
 */
async function manifestFromHosted(resource, spec, ctx, sub, rest) {
  const base = ctx.baseUrl;
  if (sub === "list") {
    const data = await apiGet(base, `/${spec.apiPath}`);
    if (ctx.json) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      const entries = /** @type {Array<{ id: string; title: string; path: string; tags?: string[] }>} */ (
        data[spec.arrayKey] || []
      );
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
    const data = await apiGet(base, `/${spec.apiPath}/${encodeURIComponent(id)}`);
    if (ctx.json) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      console.log(`# ${data.title} (${data.id})\n`);
      console.log(data.content);
    }
    return;
  }
  if (sub === "fetch") {
    const id = rest[0];
    if (!id) {
      console.error("fetch: id required.");
      process.exit(1);
    }
    const data = await apiPost(base, "/fetch", { kind: spec.fetchKind, id });
    if (ctx.json) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      console.log(`# ${data.title} (${data.id})\n`);
      console.log(data.content);
    }
    return;
  }
  console.error(`Unknown ${resource} subcommand: ${sub}`);
  process.exit(1);
}

/**
 * @param {"tool"|"workflow"|"collection"} resource
 */
async function manifestFromDisk(resource, root, spec, ctx, sub, rest) {
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

  if (sub === "show" || sub === "fetch") {
    const id = rest[0];
    if (!id) {
      console.error(`${sub}: id required.`);
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

  console.error(`Unknown ${resource} subcommand: ${sub}`);
  process.exit(1);
}
