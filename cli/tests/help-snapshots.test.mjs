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

/* The eight commands we care about. Some commands don't have a
 * top-level `--help` (`config` lists subcommands when called with no
 * args; `doctor --help` works directly). The `args` field is whatever
 * tokens we pass to surface that command's help text. */
const SCENARIOS = [
  { name: "init",     args: ["init", "--help"] },
  { name: "sync",     args: ["sync", "--help"] },
  { name: "verify",   args: ["verify", "--help"] },
  { name: "config",   args: ["config", "--help"] },
  { name: "lanes",    args: ["lanes", "--help"] },
  { name: "run",      args: ["run", "--help"] },
  { name: "callback", args: ["callback", "--help"] },
  { name: "doctor",   args: ["doctor", "--help"] },
];

const IA_VOCAB_COMMANDS = new Set(["lanes", "run", "callback"]);

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
    if (IA_VOCAB_COMMANDS.has(name)) {
      assert.ok(
        /Automation|Run\b/.test(r.stdout),
        `${name} --help should reference the operator IA vocabulary (Automation or Run)`,
      );
    }
  });
}

test("shipctl callback --help surfaces the new RunSummary outcome flags", () => {
  const r = runCtl(["callback", "--help"]);
  assert.equal(r.status, 0, r.stderr);
  for (const flag of ["--outcome-text", "--findings-count", "--escalation"]) {
    assert.ok(
      r.stdout.includes(flag),
      `callback --help should document ${flag}`,
    );
  }
});
