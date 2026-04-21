import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import YAML from "yaml";

import { migrateV1ToV2 } from "../lib/config/migrate.mjs";
import { validateConfig, CONFIG_SCHEMA_VERSION } from "../lib/config/schema.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-migrate-"));
}

function seedV1(dir, overrides = {}) {
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const cfg = {
    version: 1,
    shipctl_min: "0.11.2",
    preset: "monorepo",
    repo: "org/thing",
    api: { base_url: "https://ship.example.com", channel: "stable" },
    stack: {
      tracker: "linear",
      ci: "gh-actions",
      agents: ["cursor"],
      agent: { provider: "claude-code" },
      language: "multi",
      preset: "monorepo",
    },
    lanes: ["pr_review", "daily_standup", "tech_debt", "self_heal"],
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: {
      share: false,
      anonymous_id: null,
      scope: { artifact_usage: true, improvement_drafts: true, errors: false },
    },
    ...overrides,
  };
  fs.writeFileSync(file, YAML.stringify(cfg), "utf8");
  return { dir, file, cfg };
}

test("migrateV1ToV2 translates the 4 preset lanes", () => {
  const { cfg } = seedV1(mktmp());
  const res = migrateV1ToV2(cfg);
  assert.equal(res.migrated, true);
  assert.equal(res.config.version, CONFIG_SCHEMA_VERSION);
  assert.equal(res.config.lanes.pr_review.kind, "event");
  assert.equal(res.config.lanes.pr_review.on, "pull_request");
  assert.equal(res.config.lanes.daily_standup.kind, "schedule");
  assert.equal(res.config.lanes.daily_standup.cron, "0 9 * * 1-5");
  assert.equal(res.config.lanes.self_heal.on, "workflow_run");
  assert.equal(res.stub_lanes.length, 0);
});

test("migrateV1ToV2 lifts stack.agent.provider to agent.default.provider", () => {
  const { cfg } = seedV1(mktmp());
  const res = migrateV1ToV2(cfg);
  assert.equal(res.config.agent.default.provider, "claude-code");
  /* Legacy field stays in place so v1 readers keep working. */
  assert.equal(res.config.stack.agent.provider, "claude-code");
});

test("migrateV1ToV2 is idempotent on v2 input", () => {
  const v2 = { version: 2, shipctl_min: "0.12.0", lanes: {}, api: { base_url: "https://x" }, stack: { tracker: "none" }, artifacts: { pins: {} }, telemetry: { share: false } };
  const res = migrateV1ToV2(v2);
  assert.equal(res.migrated, false);
  assert.equal(res.config, v2);
});

test("migrateV1ToV2 emits a stub for unknown v1 lane names", () => {
  const { cfg } = seedV1(mktmp(), { lanes: ["pr_review", "my_custom_lane"] });
  const res = migrateV1ToV2(cfg);
  assert.ok(res.stub_lanes.includes("my_custom_lane"));
  assert.match(res.config.lanes.my_custom_lane.pattern, /TODO/);
  assert.ok(res.warnings.some((w) => w.includes("my_custom_lane")));
});

test("migrateV1ToV2 rejects non-v1 source versions explicitly", () => {
  assert.throws(() => migrateV1ToV2({ version: 99 }), /unsupported source version/);
});

test("migrated v2 config passes full validation (core lanes only)", () => {
  const { cfg } = seedV1(mktmp());
  const res = migrateV1ToV2(cfg);
  const v = validateConfig(res.config);
  assert.equal(v.ok, true, JSON.stringify(v.errors || []));
});

test("shipctl migrate --dry-run prints plan and leaves file untouched", () => {
  const { dir, file } = seedV1(mktmp());
  const original = fs.readFileSync(file, "utf8");
  const r = spawnSync(
    process.execPath,
    [SHIPCTL_BIN, "migrate", "--dry-run", "--cwd", dir],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /Proposed migration/);
  assert.equal(fs.readFileSync(file, "utf8"), original, "file must not change on --dry-run");
  assert.ok(!fs.existsSync(`${file}.bak`), "no backup on --dry-run");
});

test("shipctl migrate --yes writes the file and backup", () => {
  const { dir, file } = seedV1(mktmp());
  const r = spawnSync(
    process.execPath,
    [SHIPCTL_BIN, "migrate", "--yes", "--cwd", dir],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);
  assert.ok(fs.existsSync(`${file}.bak`), ".bak must be written");
  const written = YAML.parse(fs.readFileSync(file, "utf8"));
  assert.equal(written.version, CONFIG_SCHEMA_VERSION);
  assert.equal(written.lanes.pr_review.kind, "event");
  assert.equal(written.agent.default.provider, "claude-code");
});

test("shipctl migrate --json emits structured summary", () => {
  const { dir } = seedV1(mktmp());
  const r = spawnSync(
    process.execPath,
    [SHIPCTL_BIN, "migrate", "--dry-run", "--json", "--cwd", dir],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);
  const parsed = JSON.parse(r.stdout);
  assert.equal(parsed.migrated, true);
  assert.equal(parsed.proposed_config.version, CONFIG_SCHEMA_VERSION);
});

test("shipctl migrate on an already-v2 repo is a no-op", () => {
  const dir = mktmp();
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    YAML.stringify({
      version: 2,
      shipctl_min: "0.12.0",
      api: { base_url: "https://ship.example.com" },
      stack: { tracker: "none", ci: "manual", preset: "adoption-minimum", language: "multi" },
      agent: { default: { provider: null }, overrides: {} },
      lanes: {},
      artifacts: { pins: {}, auto_update: true },
      cache: { vcs_tracked: false },
      telemetry: { share: false, anonymous_id: null, scope: { artifact_usage: true, improvement_drafts: true, errors: false } },
    }),
    "utf8",
  );
  const r = spawnSync(
    process.execPath,
    [SHIPCTL_BIN, "migrate", "--yes", "--cwd", dir],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /already at the latest/);
});
