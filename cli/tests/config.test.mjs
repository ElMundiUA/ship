import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import YAML from "yaml";

import {
  DEFAULT_CONFIG,
  ensureAnonymousId,
  findShipRoot,
  readConfig,
  writeConfig,
} from "../lib/config/io.mjs";
import {
  validateConfig,
  CONFIG_SCHEMA_VERSION,
  UUID_V4_REGEX,
} from "../lib/config/schema.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-config-"));
}

function runCtl(args, { cwd } = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd: cwd || process.cwd(),
    encoding: "utf8",
  });
}

test("DEFAULT_CONFIG round-trips through validate", () => {
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  cfg.telemetry.share = true;
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res));
});

test("DEFAULT_CONFIG writes + reads back to disk", () => {
  const dir = mktmp();
  const file = path.join(dir, ".ship", "config.yml");
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  writeConfig(file, cfg);

  assert.ok(fs.existsSync(file));
  const parsed = YAML.parse(fs.readFileSync(file, "utf8"));
  assert.equal(parsed.version, CONFIG_SCHEMA_VERSION);
  assert.deepEqual(parsed.stack.agents, []);
  assert.equal(parsed.telemetry.share, false);
  assert.ok(UUID_V4_REGEX.test(parsed.telemetry.anonymous_id));

  const { config: readBack } = readConfig(dir);
  const res = validateConfig(readBack);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
});

test("validator rejects invalid stack.tracker", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.stack.tracker = "linerar";
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("stack.tracker")));
});

test("config init auto-generates anonymous_id", () => {
  const dir = mktmp();
  spawnSync("git", ["init", "-q"], { cwd: dir });
  const r = runCtl(["config", "init", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  const parsed = YAML.parse(fs.readFileSync(path.join(dir, ".ship", "config.yml"), "utf8"));
  assert.ok(UUID_V4_REGEX.test(parsed.telemetry.anonymous_id));

  const root = findShipRoot(dir);
  assert.equal(root, dir);

  const stateFile = path.join(dir, ".ship", "state.json");
  assert.ok(fs.existsSync(stateFile));
  assert.ok(fs.existsSync(path.join(dir, ".ship", "cache", ".gitkeep")));
  const gi = fs.readFileSync(path.join(dir, ".gitignore"), "utf8");
  assert.ok(gi.includes(".ship/cache/"));
});

test("config set stack.agents parses [a,b,c] as array and validates", () => {
  const dir = mktmp();
  spawnSync("git", ["init", "-q"], { cwd: dir });
  const r0 = runCtl(["config", "init", "--cwd", dir]);
  assert.equal(r0.status, 0, r0.stderr);

  const r = runCtl(["config", "set", "stack.agents", "[cursor,codex]", "--cwd", dir]);
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  const r2 = runCtl(["config", "get", "stack.agents", "--cwd", dir]);
  assert.equal(r2.status, 0);
  assert.deepEqual(JSON.parse(r2.stdout.trim()), ["cursor", "codex"]);

  const parsed = YAML.parse(fs.readFileSync(path.join(dir, ".ship", "config.yml"), "utf8"));
  assert.deepEqual(parsed.stack.agents, ["cursor", "codex"]);

  const res = validateConfig(parsed);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
});

test("config set rejects invalid enum", () => {
  const dir = mktmp();
  spawnSync("git", ["init", "-q"], { cwd: dir });
  runCtl(["config", "init", "--cwd", dir]);
  const r = runCtl(["config", "set", "stack.tracker", "bogus", "--cwd", dir]);
  assert.notEqual(r.status, 0);
  assert.ok(r.stderr.includes("stack.tracker"));
});

test("config get on missing key fails with exit 1", () => {
  const dir = mktmp();
  spawnSync("git", ["init", "-q"], { cwd: dir });
  runCtl(["config", "init", "--cwd", dir]);
  const r = runCtl(["config", "get", "nope.nothing", "--cwd", dir]);
  assert.equal(r.status, 1);
  assert.ok(r.stderr.includes("unknown key"));
});

test("pins validated against kind/id pattern and semver-ish value", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.artifacts.pins["pattern/cloud-developer"] = "1.4.2";
  cfg.artifacts.pins["workflow/scheduled-sdlc-lane"] = "~2.1";
  const ok = validateConfig(cfg);
  assert.equal(ok.ok, true, JSON.stringify(ok));

  const bad = DEFAULT_CONFIG();
  bad.artifacts.pins["WRONG:key"] = "1.0.0";
  const r = validateConfig(bad);
  assert.equal(r.ok, false);
});
