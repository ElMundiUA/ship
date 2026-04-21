import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import YAML from "yaml";

import { renderWrapper } from "../lib/commands/lanes.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-lanes-"));
}

/** Seed a minimal v2 config with the four canonical lane kinds. */
function seedRepo(dir) {
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const cfg = {
    version: 2,
    shipctl_min: "0.12.0",
    preset: "monorepo",
    repo: "org/thing",
    api: { base_url: "https://ship.example.com", channel: "stable" },
    stack: {
      tracker: "linear",
      ci: "gh-actions",
      agents: ["cursor"],
      language: "multi",
      preset: "monorepo",
    },
    agent: { default: { provider: "cursor-cloud" }, overrides: {} },
    lanes: {
      seed_knowledge_starters: {
        kind: "once",
        pattern: "seed-knowledge-starters",
        idempotency: { key: "seed-knowledge-starters.v1", store: "file", reset_on: "version-change" },
      },
      pr_review: {
        kind: "event",
        pattern: "catalog-a5-pr-self-review",
        on: "pull_request",
        permissions: { contents: "read", "pull-requests": "write" },
      },
      daily_standup: {
        kind: "schedule",
        pattern: "catalog-a13-daily-retro",
        cron: "0 9 * * 1-5",
      },
    },
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: { share: false, anonymous_id: null, scope: { artifact_usage: true } },
  };
  fs.writeFileSync(file, YAML.stringify(cfg), "utf8");
  return { dir, file, cfg };
}

function runShipctl(cwd, args, env = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd,
    env: { ...process.env, ...env },
    encoding: "utf8",
  });
}

/* ──────────────────────────── renderWrapper unit ─────────────────────────── */

test("renderWrapper emits valid YAML for kind=once", () => {
  const out = renderWrapper({
    laneId: "seed_knowledge_starters",
    lane: { kind: "once", pattern: "seed-knowledge-starters" },
    reusable: "ElMundiUA/ship/.github/workflows/run-agent.yml@v0.12.0",
    shipctlVersion: "latest",
  });
  assert.equal(out.error, undefined);
  assert.match(out.content, /ship-cli: lanes v1/);
  const parsed = YAML.parse(stripBanner(out.content));
  assert.equal(parsed.name, "Ship · seed_knowledge_starters");
  assert.ok(parsed.on.workflow_dispatch, "once lane exposes workflow_dispatch");
  assert.equal(parsed.jobs.run.uses, "ElMundiUA/ship/.github/workflows/run-agent.yml@v0.12.0");
  assert.equal(parsed.jobs.run.with.lane, "seed_knowledge_starters");
  assert.equal(parsed.jobs.run.with.shipctl_version, "latest");
  assert.equal(parsed.jobs.run.secrets, "inherit");
});

test("renderWrapper emits schedule trigger for kind=schedule", () => {
  const out = renderWrapper({
    laneId: "daily_standup",
    lane: { kind: "schedule", cron: "0 9 * * 1-5", pattern: "catalog-a13-daily-retro" },
    reusable: "owner/repo/.github/workflows/run-agent.yml@main",
    shipctlVersion: "0.12.0",
  });
  const parsed = YAML.parse(stripBanner(out.content));
  assert.deepEqual(parsed.on.schedule, [{ cron: "0 9 * * 1-5" }]);
  assert.ok(parsed.on.workflow_dispatch, "schedule lanes also allow manual dispatch");
});

test("renderWrapper emits event trigger for kind=event and honours permissions", () => {
  const out = renderWrapper({
    laneId: "pr_review",
    lane: {
      kind: "event",
      on: "pull_request",
      pattern: "p",
      permissions: { contents: "read", "pull-requests": "write" },
    },
    reusable: "owner/repo/.github/workflows/run-agent.yml@v1",
    shipctlVersion: "latest",
  });
  const parsed = YAML.parse(stripBanner(out.content));
  assert.ok("pull_request" in parsed.on, "event lane fires on its declared event");
  assert.equal(parsed.permissions["pull-requests"], "write");
});

test("renderWrapper rejects malformed lanes", () => {
  assert.ok(renderWrapper({ laneId: "x", lane: { kind: "schedule" }, reusable: "r", shipctlVersion: "latest" }).error);
  assert.ok(renderWrapper({ laneId: "x", lane: { kind: "event" }, reusable: "r", shipctlVersion: "latest" }).error);
  assert.ok(renderWrapper({ laneId: "x", lane: { kind: "bogus" }, reusable: "r", shipctlVersion: "latest" }).error);
});

/* ─────────────────────────── shipctl lanes CLI ───────────────────────────── */

test("shipctl lanes install creates one wrapper per declared lane", () => {
  const { dir } = seedRepo(mktmp());
  const res = runShipctl(dir, ["lanes", "install", "--json"]);
  assert.equal(res.status, 0, `stderr: ${res.stderr}`);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.ok, true);
  assert.equal(summary.installed.length, 3, "3 lanes → 3 wrappers");
  for (const row of summary.installed) {
    const abs = path.join(dir, row.path);
    assert.ok(fs.existsSync(abs), `wrote ${row.path}`);
    const parsed = YAML.parse(stripBanner(fs.readFileSync(abs, "utf8")));
    assert.equal(parsed.jobs.run.with.lane, row.lane);
    assert.match(parsed.jobs.run.uses, /ElMundiUA\/ship\/\.github\/workflows\/run-agent\.yml@v0\.12\.0/);
  }
});

test("shipctl lanes install is idempotent (re-run reports up-to-date)", () => {
  const { dir } = seedRepo(mktmp());
  assert.equal(runShipctl(dir, ["lanes", "install", "--json"]).status, 0);
  const second = runShipctl(dir, ["lanes", "install", "--json"]);
  assert.equal(second.status, 0);
  const summary = JSON.parse(second.stdout);
  assert.equal(summary.installed.length, 0);
  assert.equal(summary.skipped.length, 3);
  assert.ok(summary.skipped.every((r) => r.reason === "up-to-date"));
});

test("shipctl lanes install --only filters lane ids", () => {
  const { dir } = seedRepo(mktmp());
  const res = runShipctl(dir, [
    "lanes",
    "install",
    "--only",
    "seed_knowledge_starters,pr_review",
    "--json",
  ]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.installed.length, 2);
  const written = new Set(summary.installed.map((r) => r.lane));
  assert.ok(written.has("seed_knowledge_starters"));
  assert.ok(written.has("pr_review"));
  assert.ok(!written.has("daily_standup"));
});

test("shipctl lanes install --dry-run writes nothing", () => {
  const { dir } = seedRepo(mktmp());
  const res = runShipctl(dir, ["lanes", "install", "--dry-run", "--json"]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.dry_run, true);
  assert.equal(summary.installed.length, 3);
  assert.ok(summary.installed.every((r) => r.action === "would-write"));
  const wfDir = path.join(dir, ".github", "workflows");
  assert.ok(!fs.existsSync(wfDir) || fs.readdirSync(wfDir).length === 0);
});

test("shipctl lanes install --ref overrides the pinned ref", () => {
  const { dir } = seedRepo(mktmp());
  const res = runShipctl(dir, ["lanes", "install", "--ref", "main", "--json"]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  assert.match(summary.reusable, /@main$/);
  const wrapper = fs.readFileSync(
    path.join(dir, ".github", "workflows", "ship-pr_review.yml"),
    "utf8",
  );
  assert.match(wrapper, /run-agent\.yml@main/);
});

test("shipctl lanes install refuses to overwrite foreign files without --force", () => {
  const { dir } = seedRepo(mktmp());
  const wfDir = path.join(dir, ".github", "workflows");
  fs.mkdirSync(wfDir, { recursive: true });
  const foreign = path.join(wfDir, "ship-pr_review.yml");
  fs.writeFileSync(foreign, "name: manual\non: push\njobs: {}\n", "utf8");

  const res = runShipctl(dir, ["lanes", "install", "--json"]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  const skipped = summary.skipped.find((r) => r.lane === "pr_review");
  assert.ok(skipped, "hand-written ship-pr_review.yml is skipped");
  assert.equal(skipped.reason, "exists-without-banner");
  assert.equal(fs.readFileSync(foreign, "utf8"), "name: manual\non: push\njobs: {}\n");

  const forced = runShipctl(dir, ["lanes", "install", "--force", "--only", "pr_review", "--json"]);
  const forcedSummary = JSON.parse(forced.stdout);
  assert.equal(forcedSummary.installed.length, 1);
  assert.match(fs.readFileSync(foreign, "utf8"), /ship-cli: lanes v1/);
});

test("shipctl lanes list returns the lane map under --json", () => {
  const { dir } = seedRepo(mktmp());
  const res = runShipctl(dir, ["lanes", "list", "--json"]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.lanes.length, 3);
  const bySlug = Object.fromEntries(summary.lanes.map((l) => [l.lane, l]));
  assert.equal(bySlug.pr_review.kind, "event");
  assert.equal(bySlug.pr_review.on, "pull_request");
  assert.equal(bySlug.daily_standup.cron, "0 9 * * 1-5");
  assert.equal(bySlug.seed_knowledge_starters.idempotency_key, "seed-knowledge-starters.v1");
});

test("shipctl lanes remove deletes generated wrappers and leaves foreign files alone", () => {
  const { dir } = seedRepo(mktmp());
  runShipctl(dir, ["lanes", "install", "--json"]);
  const foreign = path.join(dir, ".github", "workflows", "ship-external.yml");
  fs.writeFileSync(foreign, "name: manual\n", "utf8");

  const res = runShipctl(dir, ["lanes", "remove", "--json"]);
  assert.equal(res.status, 0);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.removed.length, 3);
  assert.ok(fs.existsSync(foreign), "foreign ship-*.yml survives remove");
  assert.ok(!fs.existsSync(path.join(dir, ".github", "workflows", "ship-pr_review.yml")));
});

test("shipctl lanes fails loudly when the repo has no .ship/config.yml", () => {
  const dir = mktmp();
  const res = runShipctl(dir, ["lanes", "list", "--json"]);
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /No \.ship\/config\.yml/);
});

function stripBanner(content) {
  // YAML.parse tolerates leading comment lines; we strip them only in tests
  // where we want to inspect the structural body cleanly.
  return content.replace(/^(#.*\n)+/, "");
}
