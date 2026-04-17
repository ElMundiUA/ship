import fs from "node:fs";
import path from "node:path";
import { apiGet, apiPost, fetchArtifact } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { findShipRoot } from "../config/io.mjs";
import { writeCached, cachePath } from "../cache/store.mjs";
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
  ship ${resource} fetch <id> [--version V] [--print]
  ship ${resource} search <query> [--top-k N]

With a local Ship tree (cwd or SHIP_REPO): reads ${spec.manifestRel} on disk.
Otherwise: methodology API (GET /${spec.apiPath}, POST /fetch for fetch, POST /search for search).

In a Ship workspace (.ship/config.yml), 'fetch' writes the artifact to
.ship/cache/<kind>/<id>@<version>.md and prints a 'cached:' line. Pass
--print to also echo the body on stdout.

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
 * Parse `fetch`-specific flags so the hosted catalog path can honour
 * `--print`, `--version` and `--cwd` without polluting the global flag
 * extractor. Unknown flags are silently preserved as positionals (no error),
 * matching the rest of the manifest-catalog command.
 * @param {string[]} rest
 */
function parseFetchFlags(rest) {
  const out = { positional: /** @type {string[]} */ ([]), print: false, version: null, cwd: null };
  const copy = [...rest];
  while (copy.length) {
    const a = copy.shift();
    if (a === "--print") { out.print = true; continue; }
    if (a === "--version" && copy.length) { out.version = copy.shift(); continue; }
    if (a && a.startsWith("--version=")) { out.version = a.slice("--version=".length); continue; }
    if (a === "--cwd" && copy.length) { out.cwd = copy.shift(); continue; }
    if (a && a.startsWith("--cwd=")) { out.cwd = a.slice("--cwd=".length); continue; }
    out.positional.push(a);
  }
  return out;
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
    const flags = parseFetchFlags(rest);
    const id = flags.positional[0];
    if (!id) {
      console.error("fetch: id required.");
      process.exit(1);
    }

    const shipRoot = findShipRoot(flags.cwd || process.cwd());
    const wantCache = !!shipRoot;
    const wantStdoutBody = flags.print || !wantCache || ctx.json;

    // JSON output is a machine-readable mode; keep the legacy body-dump shape
    // but still persist to cache when the caller is in a Ship workspace so
    // downstream `verify` / `sync` operations see the artifact on disk.
    if (wantCache) {
      const { content, meta } = await fetchArtifact(
        base,
        spec.fetchKind,
        id,
        flags.version || undefined,
      );
      const version = meta.version || flags.version || "0.0.0";
      const writeRes = writeCached(shipRoot, spec.fetchKind, id, version, content, {
        content_sha256: meta.content_sha256,
        updated_at: meta.updated_at,
        channel: meta.channel,
        version,
        source_url: meta.source_url,
      });
      const rel = path.relative(shipRoot, writeRes.bodyPath) || cachePath(shipRoot, spec.fetchKind, id, version);
      const relDisplay = path.isAbsolute(rel) ? writeRes.bodyPath : rel;
      if (ctx.json) {
        console.log(
          JSON.stringify(
            {
              kind: spec.fetchKind,
              id,
              version,
              content_sha256: meta.content_sha256,
              cached_path: relDisplay,
              content: wantStdoutBody ? content : undefined,
            },
            null,
            2,
          ),
        );
      } else {
        console.log(`cached: ${spec.fetchKind}/${id}@${version} \u2192 ${relDisplay}`);
        if (wantStdoutBody) {
          console.log(`# ${id}@${version}\n`);
          console.log(content);
        }
      }
      return;
    }

    // Outside a Ship workspace: keep the legacy print-only behaviour and
    // nudge the user toward `shipctl config init`.
    const data = await apiPost(base, "/fetch", { kind: spec.fetchKind, id, ...(flags.version ? { version: flags.version } : {}) });
    if (ctx.json) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      console.error(
        `note: not in a Ship workspace (no .ship/config.yml found); printing body only. Run 'shipctl config init' to enable caching.`,
      );
      console.log(`# ${data.title || data.id} (${data.id})\n`);
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
