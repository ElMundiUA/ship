import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { validateConfig } from "../lib/config/schema.mjs";

const BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

test("validateConfig accepts stack.agent.provider", () => {
  const cfg = {
    version: 1,
    shipctl_min: "0.11.2",
    api: { base_url: "https://ship.example.com", channel: "stable", ttl_hours: 24, offline_ok: true },
    stack: {
      tracker: "none",
      ci: "gh-actions",
      agents: [],
      agent: { provider: "claude-code" },
      language: "multi",
      preset: "adoption-minimum",
    },
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: {
      share: false,
      anonymous_id: "00000000-0000-4000-8000-000000000001",
      scope: { artifact_usage: true, improvement_drafts: true, errors: false },
    },
  };
  const r = validateConfig(cfg);
  assert.equal(r.ok, true, JSON.stringify(r));
});

test("shipctl kickoff --help exits 0", () => {
  const r = spawnSync(process.execPath, [BIN, "kickoff", "--help"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl kickoff/);
});

test("shipctl kickoff prints body from monorepo disk", () => {
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = path.resolve(__dirname, "..", "..");
  const r = spawnSync(
    process.execPath,
    [BIN, "kickoff", "--pattern", "common-kickoff", "--cwd", repoRoot],
    { encoding: "utf8", cwd: path.join(repoRoot, "cli") },
  );
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /Ship CI kickoff/);
  assert.doesNotMatch(r.stdout, /^---\nartifact_kind:/m);
});

test("help lists kickoff", () => {
  const r = spawnSync(process.execPath, [BIN, "help"], { encoding: "utf8" });
  assert.equal(r.status, 0);
  assert.match(r.stdout, /shipctl kickoff/);
});
