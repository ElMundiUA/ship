#!/usr/bin/env node
import { extractGlobalArgv } from "../lib/config.mjs";
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

  if (cmd === "trigger") {
    const { triggerCommand } = await import("../lib/commands/trigger.mjs");
    await triggerCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "knowledge") {
    const { knowledgeCommand } = await import("../lib/commands/knowledge.mjs");
    await knowledgeCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "inbox") {
    const { inboxCommand } = await import("../lib/commands/inbox.mjs");
    await inboxCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "project") {
    const { projectCommand } = await import("../lib/commands/project.mjs");
    await projectCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "tracker") {
    const { trackerCommand } = await import("../lib/commands/tracker.mjs");
    await trackerCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "run") {
    const { runCommand } = await import("../lib/commands/run.mjs");
    await runCommand(ctx, rest);
    process.exit(0);
  }

  if (cmd === "preflight") {
    const { preflightCommand } = await import("../lib/commands/preflight.mjs");
    await preflightCommand(ctx, rest);
    process.exit(0);
  }

  console.error(`Unknown command: ${cmd}\nRun: shipctl help`);
  process.exit(1);
} catch (err) {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
}
