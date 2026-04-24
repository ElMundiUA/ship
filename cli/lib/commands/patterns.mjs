import { apiGet, apiPost } from "../http.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { searchCommand } from "./search.mjs";
import { scanArtifacts, readArtifactFile } from "../artifacts/fs-index.mjs";

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
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string} sub
 * @param {string[]} rest
 */
async function patternsFromDisk(root, ctx, sub, rest) {
  const entries = scanArtifacts(root, "pattern");

  if (sub === "list") {
    const slim = entries.map((p) => slimEntry(p));
    const out = { version: 1, description: "Patterns", patterns: slim };
    if (ctx.json) console.log(JSON.stringify(out, null, 2));
    else {
      console.log(`Patterns\n`);
      for (const p of slim) {
        console.log(`- ${p.id}`);
        console.log(`  ${p.title}`);
        const tags = (p.tags || []).join(", ");
        console.log(`  path: ${p.path}  tags: ${tags}\n`);
      }
    }
    return;
  }

  if (sub === "show" || sub === "fetch") {
    const id = rest[0];
    if (!id) {
      console.error(`${sub}: pattern id required.`);
      process.exit(1);
    }
    const entry = entries.find((e) => e.id === id);
    if (!entry) {
      console.error(`Unknown id: ${id}`);
      process.exit(1);
    }
    const file = readArtifactFile(root, "pattern", id);
    if (!file) {
      console.error(`Missing file: ${entry.path}`);
      process.exit(1);
    }
    const content = file.content;
    const full = { ...slimEntry(entry), content };
    if (ctx.json) console.log(JSON.stringify(full, null, 2));
    else {
      console.log(`# ${entry.title} (${entry.id})\n`);
      console.log(content);
    }
    return;
  }

  console.error(`Unknown pattern subcommand: ${sub}`);
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
  if (sub === "fetch") {
    const id = rest[0];
    if (!id) {
      console.error("fetch: pattern id required.");
      process.exit(1);
    }
    const data = await apiPost(base, "/fetch", { kind: "pattern", id });
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`# ${data.title} (${data.id})\n`);
      console.log(data.content);
    }
    return;
  }
  console.error(`Unknown pattern subcommand: ${sub}`);
  process.exit(1);
}

/**
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function patternCommand(ctx, args) {
  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  shipctl pattern list
  shipctl pattern show <id>
  shipctl pattern fetch <id>
  shipctl pattern search <query> [--top-k N]

With a local Ship tree (cwd or SHIP_REPO): list/show/fetch scan artifacts/patterns/<id>/ARTIFACT.md on disk.
Otherwise: methodology API (GET /patterns, POST /fetch for fetch, POST /search for search).

Plural alias: shipctl patterns …

Global flags: --base-url URL  --json`);
    return;
  }

  if (sub === "search") {
    await searchCommand(ctx, rest);
    return;
  }

  const root = resolveShipRepoRootForCatalog();
  if (root) {
    await patternsFromDisk(root, ctx, sub, rest);
  } else {
    await patternsFromHosted(ctx, sub, rest);
  }
}
