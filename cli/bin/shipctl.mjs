#!/usr/bin/env node
import { extractGlobalArgv } from "../lib/config.mjs";
import { docsCommand } from "../lib/commands/docs.mjs";
import { searchCommand } from "../lib/commands/search.mjs";
import { patternCommand } from "../lib/commands/patterns.mjs";
import { resourceManifestCommand } from "../lib/commands/manifest-catalog.mjs";
import { printHelp } from "../lib/commands/help.mjs";
import { initCommand } from "../lib/commands/init.mjs";
import { doctorCommand } from "../lib/commands/doctor.mjs";
import { getCliVersion } from "../lib/version.mjs";

const raw = process.argv.slice(2);

/* `--version` / `-v` / `version` short-circuit before normal arg parsing —
 * any tool worth its salt prints its version without the rest of the parser
 * having to work. We only fire when the version flag is the *first* token,
 * so that subcommand args like `feedback draft --version 1.0.0` are not
 * mistaken for a request to print our own version. */
if (raw[0] === "--version" || raw[0] === "-v" || raw[0] === "version") {
  console.log(getCliVersion());
  process.exit(0);
}

const { _, ...g } = extractGlobalArgv(raw);
const ctx = {
  baseUrl: g.baseUrl,
  baseUrlSource: g.baseUrlSource,
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

  if (cmd === "search") {
    await searchCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "docs") {
    await docsCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "pattern" || cmd === "patterns") {
    await patternCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "tool" || cmd === "tools") {
    await resourceManifestCommand("tool", ctx, rest);
    process.exit(0);
  }

  if (cmd === "collection" || cmd === "collections") {
    await resourceManifestCommand("collection", ctx, rest);
    process.exit(0);
  }

  if (cmd === "init") {
    await initCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "doctor") {
    await doctorCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "config") {
    const { configCommand } = await import("../lib/commands/config.mjs");
    await configCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "sync") {
    const { syncCommand } = await import("../lib/commands/sync.mjs");
    await syncCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "verify") {
    const { verifyCommand } = await import("../lib/commands/verify.mjs");
    await verifyCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "telemetry") {
    const { telemetryCommand } = await import("../lib/commands/telemetry.mjs");
    await telemetryCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "feedback") {
    const { feedbackCommand } = await import("../lib/commands/feedback.mjs");
    await feedbackCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "new") {
    const { newCommand } = await import("../lib/commands/new.mjs");
    await newCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "bootstrap") {
    const { bootstrapCommand } = await import("../lib/commands/bootstrap.mjs");
    await bootstrapCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "callback") {
    const { callbackCommand } = await import("../lib/commands/callback.mjs");
    await callbackCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "trigger") {
    const { triggerCommand } = await import("../lib/commands/trigger.mjs");
    await triggerCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "kickoff") {
    const { kickoffCommand } = await import("../lib/commands/kickoff.mjs");
    await kickoffCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "knowledge") {
    const { knowledgeCommand } = await import("../lib/commands/knowledge.mjs");
    await knowledgeCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "process") {
    const { processCommand } = await import("../lib/commands/process.mjs");
    await processCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "migrate") {
    const { migrateCommand } = await import("../lib/commands/migrate.mjs");
    await migrateCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "run" || cmd === "agent-run") {
    // ``agent-run`` is a back-compat alias for ``run`` — older trigger
    // workflows still spell it that way until they re-seed.
    const { runCommand } = await import("../lib/commands/run.mjs");
    await runCommand(ctx, rest);
    process.exit(0);
  }

  /* `lanes` is the protocol-stable name; `automations` is the
   * operator-friendly soft alias. Both dispatch to the same handler
   * indefinitely — we are not deprecating the original. */
  if (cmd === "lanes" || cmd === "automations") {
    const { lanesCommand } = await import("../lib/commands/lanes.mjs");
    await lanesCommand(ctx, rest);
    process.exit(0);
  }

  console.error(`Unknown command: ${cmd}\nRun: shipctl help`);
  process.exit(1);
} catch (err) {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
}
