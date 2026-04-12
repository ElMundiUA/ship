#!/usr/bin/env node
import { extractGlobalArgv } from "../lib/config.mjs";
import { docsCommand } from "../lib/commands/docs.mjs";
import { patternsCommand } from "../lib/commands/patterns.mjs";
import { manifestCatalogCommand } from "../lib/commands/manifest-catalog.mjs";
import { printHelp } from "../lib/commands/help.mjs";
import { initCommand } from "../lib/commands/init.mjs";

const raw = process.argv.slice(2);
const { _, ...g } = extractGlobalArgv(raw);
const ctx = {
  baseUrl: g.baseUrl,
  json: g.json,
  yes: g.yes,
  force: g.force,
  dryRun: g.dryRun,
};

const [cmd, ...rest] = _;

try {
  if (!cmd || cmd === "help" || cmd === "-h" || cmd === "--help") {
    printHelp();
    process.exit(0);
  }

  if (cmd === "docs") {
    await docsCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "patterns") {
    await patternsCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "tools") {
    await manifestCatalogCommand("tools", ctx, rest);
    process.exit(0);
  }

  if (cmd === "workflows") {
    await manifestCatalogCommand("workflows", ctx, rest);
    process.exit(0);
  }

  if (cmd === "collections") {
    await manifestCatalogCommand("collections", ctx, rest);
    process.exit(0);
  }

  if (cmd === "init") {
    await initCommand(ctx, rest);
    process.exit(0);
  }

  console.error(`Unknown command: ${cmd}\nRun: ship help`);
  process.exit(1);
} catch (err) {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
}
