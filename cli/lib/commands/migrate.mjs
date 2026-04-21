/**
 * `shipctl migrate` — upgrade `.ship/config.yml` from v1 to v2 (RFC-0007).
 *
 * The command is conservative:
 *   - Writes `.ship/config.yml.bak` before touching anything.
 *   - Refuses to overwrite without `--yes` unless `--dry-run` is set.
 *   - Emits a machine-readable summary under `--json` so adopters can
 *     wire it into their own migration runbooks.
 *
 * The actual migration rules live in `cli/lib/config/migrate.mjs`; this
 * command is the I/O and UX layer.
 */

import fs from "node:fs";
import path from "node:path";

import { readConfig, writeConfig } from "../config/io.mjs";
import { validateConfig } from "../config/schema.mjs";
import { migrateV1ToV2 } from "../config/migrate.mjs";

const EXIT_OK = 0;
const EXIT_USAGE = 2;
const EXIT_NOOP = 0;
const EXIT_VALIDATION = 1;

function printHelp() {
  console.log(`shipctl migrate — upgrade .ship/config.yml to the current schema.

USAGE
  shipctl migrate [--dry-run] [--yes] [--json] [--cwd <dir>]

FLAGS
  --dry-run   Print the proposed new config without writing to disk.
  --yes       Skip the interactive confirmation and overwrite in place
              (a backup is always written to .ship/config.yml.bak first).
  --json      Emit a structured summary (path, backup, warnings, stubs).
  --cwd <dir> Repo root (default: search upward for .ship/config.yml).
  --help      Show this help.

EXIT
  0  migration applied (or already at the latest schema)
  1  resulting config failed validation
  2  argument / IO error
`);
}

/**
 * @param {{json?: boolean, yes?: boolean, dryRun?: boolean}} ctx
 * @param {string[]} rest
 */
export async function migrateCommand(ctx, rest) {
  const args = parseArgs(rest);
  if (args.help) {
    printHelp();
    process.exit(EXIT_OK);
  }

  const cwd = args.cwd || process.cwd();
  let read;
  try {
    read = readConfig(cwd);
  } catch (err) {
    die(EXIT_USAGE, err instanceof Error ? err.message : String(err));
  }

  const { config: src, filePath } = read;

  let result;
  try {
    result = migrateV1ToV2(src);
  } catch (err) {
    die(EXIT_USAGE, `migrate failed: ${err instanceof Error ? err.message : err}`);
  }

  if (!result.migrated) {
    const payload = {
      path: filePath,
      migrated: false,
      warnings: result.warnings,
      stub_lanes: [],
    };
    if (ctx.json || args.json) {
      console.log(JSON.stringify(payload, null, 2));
    } else {
      console.log(`${filePath}: already at the latest schema (no changes).`);
    }
    process.exit(EXIT_NOOP);
  }

  const validation = validateConfig(result.config);
  if (!validation.ok) {
    const msg = [
      "migrate produced an invalid v2 config:",
      ...validation.errors.map((e) => `  - ${e}`),
      ...result.warnings.map((w) => `  (warn) ${w}`),
    ].join("\n");
    die(EXIT_VALIDATION, msg);
  }

  const yes = ctx.yes || args.yes;
  const dryRun = ctx.dryRun || args.dryRun;
  const backupPath = `${filePath}.bak`;

  if (dryRun || !yes) {
    const summary = {
      path: filePath,
      migrated: true,
      backup: backupPath,
      dry_run: Boolean(dryRun),
      warnings: result.warnings,
      stub_lanes: result.stub_lanes,
    };
    if (ctx.json || args.json) {
      console.log(
        JSON.stringify(
          { ...summary, proposed_config: result.config },
          null,
          2,
        ),
      );
    } else {
      console.log(`Proposed migration for ${filePath}:`);
      for (const w of result.warnings) console.log(`  - ${w}`);
      if (result.stub_lanes.length) {
        console.log(`  - stub lanes (fill before shipping): ${result.stub_lanes.join(", ")}`);
      }
      console.log("");
      console.log(serialiseForDisplay(result.config));
      console.log("");
      if (dryRun) {
        console.log("--dry-run: not writing to disk.");
      } else {
        console.log("Re-run with --yes to apply the migration (writes .bak first).");
      }
    }
    process.exit(EXIT_OK);
  }

  try {
    fs.copyFileSync(filePath, backupPath);
    writeConfig(filePath, result.config);
  } catch (err) {
    die(EXIT_USAGE, `migrate write failed: ${err instanceof Error ? err.message : err}`);
  }

  if (ctx.json || args.json) {
    console.log(
      JSON.stringify(
        {
          path: filePath,
          migrated: true,
          backup: backupPath,
          warnings: result.warnings,
          stub_lanes: result.stub_lanes,
        },
        null,
        2,
      ),
    );
  } else {
    console.log(`Wrote ${filePath} (backup at ${backupPath}).`);
    for (const w of result.warnings) console.log(`  - ${w}`);
    if (result.stub_lanes.length) {
      console.log(
        `  - stub lanes to finish: ${result.stub_lanes.join(", ")} — edit the file or rerun 'shipctl init'.`,
      );
    }
  }
  process.exit(EXIT_OK);
}

function parseArgs(rest) {
  const out = { dryRun: false, yes: false, json: false, help: false, cwd: null };
  const copy = [...rest];
  while (copy.length) {
    const a = copy.shift();
    if (a === "--help" || a === "-h") out.help = true;
    else if (a === "--dry-run") out.dryRun = true;
    else if (a === "--yes") out.yes = true;
    else if (a === "--json") out.json = true;
    else if (a === "--cwd" && copy[0] !== undefined) out.cwd = path.resolve(String(copy.shift()));
    else if (a && a.startsWith("--cwd=")) out.cwd = path.resolve(a.slice("--cwd=".length));
    else {
      console.error(`unknown argument: ${a}\nRun: shipctl migrate --help`);
      process.exit(EXIT_USAGE);
    }
  }
  return out;
}

/**
 * Render the config as YAML-ish for display. We deliberately don't
 * import the YAML module here — the real write path already
 * normalises, and `--dry-run` is human-scan territory. JSON is good
 * enough and shows every field unambiguously.
 */
function serialiseForDisplay(config) {
  return JSON.stringify(config, null, 2);
}

function die(code, msg) {
  console.error(msg);
  process.exit(code);
}
