import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

/* Run shipctl with the given args and capture stdout/stderr/status.
 * No network; help paths short-circuit before we ever hit IO. */
function runCtl(args, env = {}) {
  return spawnSync(process.execPath, [BIN, ...args], {
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
}

/* `feedback` and `telemetry` print a usage error on `--help` instead
 * of a help body — they're left out of this snapshot for that reason,
 * not because they're un-discoverable. `shipctl help` covers them. */
const SCENARIOS = [
  { name: "init",      args: ["init", "--help"] },
  { name: "sync",      args: ["sync", "--help"] },
  { name: "verify",    args: ["verify", "--help"] },
  { name: "config",    args: ["config", "--help"] },
  { name: "doctor",    args: ["doctor", "--help"] },
  { name: "trigger",   args: ["trigger", "--help"] },
  { name: "run",       args: ["run", "--help"] },
  { name: "knowledge", args: ["knowledge", "--help"] },
];

for (const { name, args } of SCENARIOS) {
  test(`shipctl ${name} --help: exits 0, mentions itself, mentions shipctl`, () => {
    const r = runCtl(args);
    assert.equal(r.status, 0, `${name} --help exited ${r.status}\n${r.stderr}`);
    assert.ok(r.stdout.length > 0, `${name} --help produced empty stdout`);
    assert.ok(
      r.stdout.includes(name),
      `${name} --help should mention the command name itself`,
    );
    assert.ok(
      r.stdout.includes("shipctl "),
      `${name} --help should mention 'shipctl ' (note the trailing space)`,
    );
  });
}
