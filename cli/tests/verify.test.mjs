import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

import { DEFAULT_CONFIG, ensureAnonymousId } from "../lib/config/io.mjs";
import { writeCached, cachePath } from "../lib/cache/store.mjs";

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
  writeFile(dir, ".gitignore", "# Ship\n.ship/cache/\n");
  writeFile(
    dir,
    ".cursor/rules/ship-artifacts-protocol.mdc",
    `<!-- ship-cli: artifacts-protocol v1 -->\n\nbody\n\n<!-- ship-cli:end artifacts-protocol -->\n\n<!-- ship-cli: installed-from collection/agent-rules-cursor@1.0.0 -->\n`,
  );
  // Seed the cached agent-rules artifact so rules-markers can resolve
  // `install_target` from its front-matter (Bug E).
  writeCached(
    dir,
    "collection",
    "agent-rules-cursor",
    "1.0.0",
    `---\nartifact_kind: collection\nsubkind: agent-rules\nagent_id: cursor\ninstall_target: ".cursor/rules/ship-artifacts-protocol.mdc"\n---\n\nbody\n`,
    { source_url: "about:test" },
  );

  const r = runVerify(["--cwd", dir, "--no-network", "--json"]);
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  const localFails = parsed.checks.filter(
    (c) => c.category === "local" && c.status === "fail",
  );
  assert.deepEqual(localFails, [], `unexpected local fails: ${JSON.stringify(localFails)}`);
  const rules = parsed.checks.find((c) => c.id === "rules-markers");
  assert.equal(rules.status, "pass", `expected rules-markers pass, got ${JSON.stringify(rules)}`);
});

test("rules-markers honors install_target from cached agent-rules artifact (codex→AGENTS.md)", () => {
  const dir = mktmp();
  writeConfigFixture(dir, {
    stack: { agents: ["codex"], preset: "adoption-minimum" },
  });
  writeFile(dir, ".gitignore", ".ship/cache/\n");
  writeFile(
    dir,
    "AGENTS.md",
    `# Project\n\n<!-- ship-cli: artifacts-protocol v1 -->\n\nbody\n\n<!-- ship-cli:end artifacts-protocol -->\n\n<!-- ship-cli: installed-from collection/agent-rules-codex@1.0.0 -->\n`,
  );
  writeCached(
    dir,
    "collection",
    "agent-rules-codex",
    "1.0.0",
    `---\nartifact_kind: collection\nsubkind: agent-rules\nagent_id: codex\ninstall_target: "AGENTS.md"\nmarker: "<!-- ship-cli: artifacts-protocol v1 -->"\n---\n\nbody\n`,
    { source_url: "about:test" },
  );

  const r = runVerify(["--cwd", dir, "--no-network", "--check", "rules-markers", "--json"]);
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  const row = parsed.checks.find((c) => c.id === "rules-markers");
  assert.equal(row.status, "pass", `expected rules-markers pass, got ${JSON.stringify(row)}`);
});

test("rules-markers warns (not fails) when cached agent-rules artifact is missing", () => {
  const dir = mktmp();
  writeConfigFixture(dir, {
    stack: { agents: ["cursor"], preset: "adoption-minimum" },
  });
  writeFile(dir, ".gitignore", ".ship/cache/\n");
  const r = runVerify(["--cwd", dir, "--no-network", "--check", "rules-markers", "--json"]);
  const parsed = JSON.parse(r.stdout);
  const row = parsed.checks.find((c) => c.id === "rules-markers");
  assert.equal(row.status, "warn", JSON.stringify(row));
  assert.match(row.detail, /no cached agent-rules-cursor/);
});

test("cache-integrity: tampered body fails check", () => {
  const dir = mktmp();
  writeConfigFixture(dir);
  const written = writeCached(dir, "collection", "demo", "1.0.0", "hello world", {
    source_url: "about:test",
  });
  assert.ok(fs.existsSync(written.bodyPath));
  // Tamper: overwrite body without updating meta.
  fs.writeFileSync(cachePath(dir, "collection", "demo", "1.0.0"), "tampered contents", "utf8");

  const r = runVerify(["--cwd", dir, "--no-network", "--check", "cache-integrity", "--json"]);
  const parsed = JSON.parse(r.stdout);
  const row = parsed.checks.find((c) => c.id === "cache-integrity");
  assert.equal(row.status, "fail");
  assert.equal(r.status, 1);
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

test("--severity warn hides pass rows in human output", () => {
  const dir = mktmp();
  writeConfigFixture(dir, {
    stack: { agents: ["cursor"], preset: "adoption-minimum" },
  });
  writeFile(dir, ".gitignore", ".ship/cache/\n");
  writeFile(
    dir,
    ".cursor/rules/ship-artifacts-protocol.mdc",
    `<!-- ship-cli: artifacts-protocol v1 -->\n\nbody\n\n<!-- ship-cli: installed-from collection/agent-rules-cursor@1.0.0 -->\n`,
  );
  const r = runVerify(["--cwd", dir, "--no-network", "--severity", "warn"]);
  assert.equal(r.status, 0, r.stderr || r.stdout);
  assert.doesNotMatch(r.stdout, /\[pass\]/);
});

test("verify help prints and lists checks", () => {
  const r = spawnSync(process.execPath, [bin, "verify", "--help"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl verify/);
  assert.match(r.stdout, /config-present/);
  assert.match(r.stdout, /tracker-labels/);
});
