import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bin = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

function run(script, args) {
  return spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
}

test("shipctl help exits 0 and mentions shipctl", () => {
  const r = run(bin, ["help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl/);
  assert.match(r.stdout, /Artifacts protocol|artifacts protocol/i);
  assert.doesNotMatch(r.stdout, /^\s*ship\s+(init|search|docs|pattern)\b/m);
});

test("shipctl init help mentions new flags", () => {
  const r = run(bin, ["init", "--help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /--agents/);
  assert.match(r.stdout, /--tracker/);
  assert.match(r.stdout, /--ci/);
  assert.match(r.stdout, /--preset/);
  assert.match(r.stdout, /--copy-playbook/);
  assert.match(r.stdout, /shipctl init/);
});

