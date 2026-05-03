import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

import { DEFAULT_CONFIG } from "../lib/config/schema.mjs";
import { ensureAnonymousId } from "../lib/config/io.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bin = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-verify-"));
}

function writeFile(dir, rel, contents) {
  const full = path.join(dir, rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, contents, "utf8");
  return full;
}

function runVerify(args, { cwd } = {}) {
  return spawnSync(
    process.execPath,
    [bin, "verify", ...args],
    { cwd: cwd || process.cwd(), encoding: "utf8" },
  );
}

function writeConfigFixture(dir, overrides = {}) {
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  if (overrides.stack) Object.assign(cfg.stack, overrides.stack);
  if (overrides.api) Object.assign(cfg.api, overrides.api);
  writeFile(dir, ".ship/config.yml", YAML.stringify(cfg));
  return cfg;
}

test("verify on empty repo: config-present fails, exit 1, JSON valid", () => {
  const dir = mktmp();
  const r = runVerify(["--cwd", dir, "--no-network", "--json"]);
  assert.equal(r.status, 1, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  assert.equal(parsed.summary.fail >= 1, true);
  const cfgRow = parsed.checks.find((c) => c.id === "config-present");
  assert.ok(cfgRow, "config-present row should be present");
  assert.equal(cfgRow.status, "fail");
  assert.equal(parsed.exit_code, 1);
});

test("verify post-init fixture: all local checks pass", () => {
  const dir = mktmp();
  writeConfigFixture(dir, {
    stack: { agents: ["cursor"], preset: "adoption-minimum" },
  });
  // The seed PR (Phase 2.5) drops a Cursor rule file at this path —
  // the agents-on-disk check uses heuristic detection, so an empty
  // ``.cursor/`` dir is enough.
  writeFile(dir, ".cursor/rules/ship-artifacts-protocol.mdc", "body\n");

  const r = runVerify(["--cwd", dir, "--no-network", "--json"]);
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  const localFails = parsed.checks.filter(
    (c) => c.category === "local" && c.status === "fail",
  );
  assert.deepEqual(localFails, [], `unexpected local fails: ${JSON.stringify(localFails)}`);
});

test("--no-network skips network checks (does not call them)", () => {
  const dir = mktmp();
  writeConfigFixture(dir);
  const r = runVerify(["--cwd", dir, "--no-network", "--json"]);
  const parsed = JSON.parse(r.stdout);
  const networkRows = parsed.checks.filter((c) => c.category === "network");
  assert.ok(networkRows.length > 0, "should still include network rows (with skip status)");
  for (const row of networkRows) {
    assert.equal(row.status, "skip", `network check ${row.id} was not skipped`);
    assert.match(row.detail, /--no-network/);
  }
});

test("--json output is valid JSON and contains required top-level keys", () => {
  const dir = mktmp();
  writeConfigFixture(dir);
  const r = runVerify(["--cwd", dir, "--no-network", "--json"]);
  const parsed = JSON.parse(r.stdout);
  assert.ok(Array.isArray(parsed.checks));
  assert.equal(typeof parsed.summary.total, "number");
  assert.equal(typeof parsed.exit_code, "number");
});

test("verify help prints and lists checks", () => {
  const r = spawnSync(process.execPath, [bin, "verify", "--help"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl verify/);
  assert.match(r.stdout, /config-present/);
  assert.match(r.stdout, /tracker-labels/);
});
