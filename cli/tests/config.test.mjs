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
  cfg.artifacts.pins["pattern/role-developer"] = "1.4.2";
  cfg.artifacts.pins["tool/methodology-api"] = "~2.1";
  const ok = validateConfig(cfg);
  assert.equal(ok.ok, true, JSON.stringify(ok));

  const bad = DEFAULT_CONFIG();
  bad.artifacts.pins["WRONG:key"] = "1.0.0";
  const r = validateConfig(bad);
  assert.equal(r.ok, false);

  // Phase 6 retired ``workflow`` as a pinnable kind.
  const retired = DEFAULT_CONFIG();
  retired.artifacts.pins["workflow/scheduled-sdlc-lane"] = "1.0.0";
  const r2 = validateConfig(retired);
  assert.equal(r2.ok, false);
});

/* ------------------------------------------------------------------ */
/* v2 lanes (RFC-0007)                                                 */
/* ------------------------------------------------------------------ */

test("v2 config with a full lanes map validates cleanly", () => {
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  cfg.lanes = {
    seed_knowledge_starters: {
      kind: "once",
      pattern: "onboard-seed-knowledge",
      idempotency: {
        key: "onboard-seed-knowledge.v1",
        store: "file",
        reset_on: "version-change",
      },
    },
    pr_review: {
      kind: "event",
      pattern: "flow-pr-self-review",
      on: "pull_request",
      permissions: { contents: "read", "pull-requests": "write" },
    },
    daily_standup: {
      kind: "schedule",
      pattern: "flow-daily-retro",
      cron: "0 9 * * 1-5",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
});

test("v4 wizard seed lane shape validates cleanly", () => {
  const cfg = YAML.parse(`
preset: default
version: 2
shipctl_min: 0.12.0
repo: acme/widgets
api:
  base_url: "https://ship.elmundi.com"
  channel: stable
  ttl_hours: 24
  offline_ok: true
stack:
  tracker: none
  ci: gh-actions
  agents: []
  language: multi
agent:
  default: {}
  overrides: {}
lanes:
  pr_review:
    kind: event
    on: pull_request
    pattern: "**"
  scan-security-deps:
    kind: schedule
    cron: "0 7 * * *"
    pattern: scan-security-deps
  daily_standup:
    kind: schedule
    cron: "0 9 * * 1-5"
    pattern: flow-daily-retro
  self_heal:
    kind: schedule
    cron: "0 4 * * *"
    pattern: op-workflow-self-heal
`);
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
});

test("v2 rejects unknown lane kind", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { bad: { kind: "interval", pattern: "x" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("lanes.bad.kind")));
});

test("v2 `once` lane missing idempotency fails", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { seed: { kind: "once", pattern: "seed-x" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("idempotency")));
});

test("v2 `schedule` lane rejects malformed cron", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { d: { kind: "schedule", pattern: "p", cron: "daily please" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("cron")));
});

test("v2 `event` lane rejects unknown `on`", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { e: { kind: "event", pattern: "p", on: "issue_comment" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("lanes.e.on")));
});

test("v2 lane id regex enforced", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { "Has Space": { kind: "once", pattern: "x", idempotency: { key: "k" } } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("invalid id")));
});

/* ------------------------------------------------------------------ */
/* RFC-0008 C3.1 — lanes.<id>.patterns: [ids] (multi-pattern lanes)    */
/* ------------------------------------------------------------------ */

test("v2 lane accepts `patterns: [ids]` canonical form", () => {
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  cfg.lanes = {
    tech_debt_audit: {
      kind: "schedule",
      patterns: ["role-tech-architect", "role-qa-architect", "role-security-officer"],
      cron: "0 6 * * 1",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
});

test("v2 lane rejects empty `patterns` list", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { a: { kind: "event", patterns: [], on: "pull_request" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("patterns: must contain at least one")));
});

test("v2 lane rejects both `pattern` and `patterns`", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = {
    a: {
      kind: "event",
      pattern: "flow-pr-self-review",
      patterns: ["flow-pr-self-review"],
      on: "pull_request",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("not both")));
});

test("v2 lane rejects non-string entry in `patterns`", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { a: { kind: "event", patterns: ["x", 42], on: "pull_request" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("patterns[1]")));
});

test("v2 lane requires either `pattern` or `patterns`", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = { a: { kind: "event", on: "pull_request" } };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("must declare 'pattern'")));
});

test("lanePatterns() normalises both shapes to a list", async () => {
  const { lanePatterns, lanePrimaryPattern } = await import("../lib/config/schema.mjs");
  assert.deepEqual(lanePatterns({ pattern: "a" }), ["a"]);
  assert.deepEqual(lanePatterns({ patterns: ["a", "b"] }), ["a", "b"]);
  assert.deepEqual(lanePatterns({}), []);
  assert.equal(lanePrimaryPattern({ patterns: ["x", "y"] }), "x");
  assert.equal(lanePrimaryPattern({ pattern: "z" }), "z");
  assert.equal(lanePrimaryPattern({}), null);
});

/* -------------------------------------------------------------------- */
/* RFC-0008 C3.2 — lane.fanout (multi-pattern execution strategy)       */
/* -------------------------------------------------------------------- */

test("v2 lane accepts fanout=matrix (default)", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = {
    a: {
      kind: "schedule",
      cron: "0 6 * * 1",
      patterns: ["role-tech-architect", "role-qa-architect"],
      fanout: "matrix",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res.errors));
});

test("v2 lane accepts fanout=sequential and fanout=concurrent", () => {
  for (const mode of ["sequential", "concurrent"]) {
    const cfg = DEFAULT_CONFIG();
    cfg.lanes = {
      a: {
        kind: "schedule",
        cron: "0 6 * * 1",
        patterns: ["role-tech-architect", "role-qa-architect"],
        fanout: mode,
      },
    };
    const res = validateConfig(cfg);
    assert.equal(res.ok, true, `mode=${mode}: ${JSON.stringify(res.errors)}`);
  }
});

test("v2 lane rejects unknown fanout values", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = {
    a: {
      kind: "schedule",
      cron: "0 6 * * 1",
      patterns: ["role-tech-architect", "role-qa-architect"],
      fanout: "magical",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes("fanout: must be one of")));
});

test("v2 lane warns when fanout is set on a single-pattern lane", () => {
  const cfg = DEFAULT_CONFIG();
  cfg.lanes = {
    a: {
      kind: "event",
      on: "pull_request",
      pattern: "flow-pr-self-review",
      fanout: "sequential",
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, true);
  assert.ok(res.warnings.some((w) => w.includes("fanout: ignored for single-pattern")));
});

test("laneFanout() resolves to default when unset or invalid", async () => {
  const { laneFanout, LANE_FANOUT_DEFAULT } = await import("../lib/config/schema.mjs");
  assert.equal(laneFanout({}), LANE_FANOUT_DEFAULT);
  assert.equal(laneFanout(null), LANE_FANOUT_DEFAULT);
  assert.equal(laneFanout({ fanout: "matrix" }), "matrix");
  assert.equal(laneFanout({ fanout: "sequential" }), "sequential");
  assert.equal(laneFanout({ fanout: "concurrent" }), "concurrent");
  assert.equal(laneFanout({ fanout: "bogus" }), LANE_FANOUT_DEFAULT);
});

test("v1 config is accepted with a deprecation warning", () => {
  const cfg = {
    version: 1,
    shipctl_min: "0.11.2",
    api: { base_url: "https://ship.example.com", channel: "stable", ttl_hours: 24, offline_ok: true },
    stack: {
      tracker: "none",
      ci: "manual",
      agents: [],
      language: "multi",
      preset: "adoption-minimum",
    },
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: {
      share: false,
      anonymous_id: null,
      scope: { artifact_usage: true, improvement_drafts: true, errors: false },
    },
  };
  const res = validateConfig(cfg);
  assert.equal(res.ok, true, JSON.stringify(res.errors || []));
  assert.ok(res.warnings.some((w) => w.includes("shipctl migrate")));
});

test("write/read preserves lane key order", () => {
  const dir = mktmp();
  const file = path.join(dir, ".ship", "config.yml");
  const cfg = DEFAULT_CONFIG();
  ensureAnonymousId(cfg);
  cfg.lanes = {
    z_first: {
      timeout_minutes: 15,
      kind: "once",
      idempotency: { reset_on: "manual", key: "z.v1", store: "file" },
      pattern: "p-z",
    },
    a_second: {
      on: "pull_request",
      pattern: "p-a",
      kind: "event",
    },
  };
  writeConfig(file, cfg);
  const raw = fs.readFileSync(file, "utf8");

  /* Lane ids preserve insertion order (z_first before a_second). */
  assert.ok(raw.indexOf("z_first:") < raw.indexOf("a_second:"));
  /* Inside each lane, `kind` and `pattern` come before any later fields. */
  const zBlock = raw.slice(raw.indexOf("z_first:"), raw.indexOf("a_second:"));
  assert.ok(zBlock.indexOf("kind:") < zBlock.indexOf("pattern:"));
  assert.ok(zBlock.indexOf("pattern:") < zBlock.indexOf("idempotency:"));
  assert.ok(zBlock.indexOf("idempotency:") < zBlock.indexOf("timeout_minutes:"));
  /* idempotency keys re-ordered to key/store/reset_on. */
  assert.ok(zBlock.indexOf("key:") < zBlock.indexOf("store:"));
  assert.ok(zBlock.indexOf("store:") < zBlock.indexOf("reset_on:"));
});
