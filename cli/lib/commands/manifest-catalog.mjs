import path from "node:path";
import { apiGet, apiPost, fetchArtifact } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { findShipRoot } from "../config/io.mjs";
import { writeCached, cachePath } from "../cache/store.mjs";
import { searchCommand } from "./search.mjs";
import { scanArtifacts, readArtifactFile, pluralFor } from "../artifacts/fs-index.mjs";

/** @type {Record<string, { arrayKey: string; name: string; apiPath: string; fetchKind: "tool"|"collection" }>} */
const RESOURCES = {
  tool: {
    arrayKey: "tools",
    name: "Tools",
    apiPath: "tools",
    fetchKind: "tool",
  },
  collection: {
    arrayKey: "collections",
    name: "Collections",
    apiPath: "collections",
    fetchKind: "collection",
  },
};

/**
 * @param {"tool"|"collection"} resource
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function resourceManifestCommand(resource, ctx, args) {
  const spec = RESOURCES[resource];
  if (!spec) throw new Error(`Unknown resource: ${resource}`);

  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    const plural = pluralFor(spec.fetchKind);
    console.log(`Usage:
  ship ${resource} list
  ship ${resource} show <id>
  ship ${resource} fetch <id> [--version V] [--print]
  ship ${resource} search <query> [--top-k N]

With a local Ship tree (cwd or SHIP_REPO): scans artifacts/${plural}/<id>/ARTIFACT.md on disk.
Otherwise: methodology API (GET /${spec.apiPath}, POST /fetch for fetch, POST /search for search).

In a Ship workspace (.ship/config.yml), 'fetch' writes the artifact to
.ship/cache/<kind>/<id>@<version>/ARTIFACT.md and prints a 'cached:' line. Pass
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
 * @param {"tool"|"collection"} resource
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
 * @param {"tool"|"collection"} resource
 */
async function manifestFromDisk(resource, root, spec, ctx, sub, rest) {
  const entries = scanArtifacts(root, spec.fetchKind);

  if (sub === "list") {
    if (ctx.json) {
      const payload = {
        description: spec.name,
        version: 1,
        [spec.arrayKey]: entries,
      };
      console.log(JSON.stringify(payload, null, 2));
    } else {
      console.log(`${spec.name}\n`);
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
    const file = readArtifactFile(root, spec.fetchKind, id);
    if (!file) {
      console.error(`Missing file: ${entry.path}`);
      process.exit(1);
    }
    const content = file.content;
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
