/**
 * `shipctl kickoff` — print the markdown body of the `kickoff` pattern
 * (or another pattern id) for piping into the customer’s agent in CI.
 *
 * Resolution order for the methodology host:
 *   1. Global `--base-url` (methodology API root, same as `pattern fetch`).
 *   2. `.ship/config.yml` → `api.base_url` with `/api/methodology` appended
 *      when absent.
 *   3. `SHIP_API_BASE` / default public host.
 *
 * When the process cwd (or `--cwd`) is inside the Ship monorepo, we read
 * `artifacts/patterns/<id>/ARTIFACT.md` from disk so local dev matches prod.
 */

import path from "node:path";
import { fetchArtifact } from "../http.mjs";
import { readConfig, findShipRoot } from "../config/io.mjs";
import { resolveShipRepoRootForCatalog } from "../find-ship-root.mjs";
import { readArtifactFile } from "../artifacts/fs-index.mjs";

function printKickoffHelp() {
  console.log(`shipctl kickoff — print a pattern body for piping into your agent (CI).

USAGE
  shipctl kickoff [--pattern <id>] [--version <semver>] [--raw] [--json] [--cwd <dir>]

DEFAULTS
  --pattern common-kickoff

FLAGS
  --pattern   Catalog pattern id (folder under artifacts/patterns/).
  --version   Optional pinned version (POST /fetch).
  --raw       Print the full ARTIFACT.md including YAML front matter.
  --json      Emit { pattern_id, body, agent_provider? } JSON.
  --cwd       Repo root to find .ship/config.yml (default: search upward).

The default output is markdown body only (front matter stripped) on stdout.
When .ship/config.yml sets stack.agent.provider, a one-line hint is written
to stderr so logs show which agent the repo is wired for — unless --json.

EXAMPLE (workflow step)
  shipctl kickoff --pattern common-kickoff > kickoff.md
  # …concatenate workload pattern + kickoff.md into your agent invocation…
`);
}

function stripFrontmatter(full) {
  if (!full || !full.startsWith("---\n")) return full;
  const end = full.indexOf("\n---\n", 4);
  if (end === -1) return full;
  return full.slice(end + "\n---\n".length);
}

/** @param {string} methodologyBase */
function resolveMethodologyBase(ctx, config) {
  const fromFlag = ctx.baseUrl;
  const raw = config?.api?.base_url;
  if (typeof raw === "string" && raw.trim()) {
    const u = raw.replace(/\/$/, "");
    return u.includes("/api/methodology") ? u : `${u}/api/methodology`;
  }
  return fromFlag;
}

function parseKickoffArgs(rest) {
  const out = {
    patternId: "common-kickoff",
    version: null,
    raw: false,
    json: false,
    help: false,
    cwd: process.cwd(),
  };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--help" || a === "-h") {
      out.help = true;
      copy.shift();
      continue;
    }
    if (a === "--raw") {
      out.raw = true;
      copy.shift();
      continue;
    }
    if (a === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (a === "--pattern" && copy[1]) {
      copy.shift();
      out.patternId = String(copy.shift());
      continue;
    }
    if (a.startsWith("--pattern=")) {
      out.patternId = a.slice("--pattern=".length);
      copy.shift();
      continue;
    }
    if (a === "--version" && copy[1]) {
      copy.shift();
      out.version = String(copy.shift());
      continue;
    }
    if (a.startsWith("--version=")) {
      out.version = a.slice("--version=".length);
      copy.shift();
      continue;
    }
    if (a === "--cwd" && copy[1]) {
      copy.shift();
      out.cwd = path.resolve(String(copy.shift()));
      continue;
    }
    if (a.startsWith("--cwd=")) {
      out.cwd = path.resolve(a.slice("--cwd=".length));
      copy.shift();
      continue;
    }
    console.error(`unknown argument: ${a}\nRun: shipctl kickoff --help`);
    process.exit(2);
  }
  return out;
}

export async function kickoffCommand(ctx, rest) {
  const args = parseKickoffArgs(rest);
  if (args.help) {
    printKickoffHelp();
    return;
  }

  const ctx2 = ctx;

  /** @type {object|null} */
  let config = null;
  const root = findShipRoot(args.cwd);
  if (root) {
    try {
      config = readConfig(root).config;
    } catch {
      config = null;
    }
  }

  const methodologyBase = resolveMethodologyBase(ctx2, config);
  const agentProvider =
    config?.stack?.agent && typeof config.stack.agent === "object"
      ? config.stack.agent.provider
      : null;

  /** @type {string|undefined} */
  let fullText;
  const shipRepo = resolveShipRepoRootForCatalog();
  if (shipRepo) {
    const file = readArtifactFile(shipRepo, "pattern", args.patternId);
    if (file) fullText = file.content;
  }
  if (fullText === undefined) {
    const { content } = await fetchArtifact(
      methodologyBase,
      "pattern",
      args.patternId,
      args.version || undefined,
    );
    fullText = content;
  }

  const body = args.raw ? fullText : stripFrontmatter(fullText);

  if (args.json) {
    console.log(
      JSON.stringify(
        {
          pattern_id: args.patternId,
          body,
          agent_provider: agentProvider || null,
        },
        null,
        2,
      ),
    );
    return;
  }

  if (agentProvider && typeof agentProvider === "string") {
    console.error(`# ship: stack.agent.provider=${agentProvider}`);
  }
  process.stdout.write(body.endsWith("\n") ? body : `${body}\n`);
}
