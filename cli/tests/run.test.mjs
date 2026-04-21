import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import YAML from "yaml";

import {
  decideRun,
  readMarker,
  resolveMarkerPath,
  sha256,
  writeMarker,
} from "../lib/state/idempotency.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

/* The tests below exercise `shipctl run` inside the Ship monorepo so
 * the pattern loader reads from `artifacts/patterns/seed-knowledge-
 * starters/ARTIFACT.md` on disk instead of hitting the network. We
 * do this by pointing `--cwd` at a synthesised workspace that has a
 * minimal v2 `.ship/config.yml` and is nested inside the real repo. */
const REPO_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-run-"));
}

function writeConfig(dir, config) {
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, YAML.stringify(config), "utf8");
  return file;
}

function baseConfig(extra = {}) {
  return {
    version: 2,
    shipctl_min: "0.12.0",
    api: { base_url: "https://ship.example.com", channel: "stable" },
    stack: { tracker: "none", ci: "manual", preset: "adoption-minimum", language: "multi" },
    agent: { default: { provider: null }, overrides: {} },
    lanes: {},
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: {
      share: false,
      anonymous_id: null,
      scope: { artifact_usage: true, improvement_drafts: true, errors: false },
    },
    ...extra,
  };
}

function runCtl(args, env = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    encoding: "utf8",
    /* Ensure the runtime thinks we're inside the Ship monorepo so the
     * local disk pattern loader short-circuits the HTTP path. */
    env: { ...process.env, SHIP_REPO: REPO_ROOT, ...env },
  });
}

test("shipctl run --help exits 0", () => {
  const r = runCtl(["run", "--help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl run/);
});

test("shipctl run rejects missing --lane", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  const r = runCtl(["run", "--cwd", dir]);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /--lane/);
});

test("shipctl run on v1 config exits 2 with migration hint", () => {
  const dir = mktmp();
  writeConfig(dir, { version: 1, shipctl_min: "0.11.2", api: { base_url: "https://x" }, stack: { tracker: "none", ci: "manual" }, artifacts: { pins: {} }, telemetry: { share: false } });
  const r = runCtl(["run", "--lane", "anything", "--cwd", dir]);
  assert.equal(r.status, 2, `${r.stdout}\n${r.stderr}`);
  assert.match(r.stderr, /shipctl migrate/);
});

test("shipctl run on unknown lane exits 1", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig({ lanes: { foo: { kind: "once", pattern: "p", idempotency: { key: "k" } } } }));
  const r = runCtl(["run", "--lane", "bar", "--cwd", dir]);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /unknown lane/);
});

test("shipctl run --dry-run prints pattern for kind=once without marker", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        seed: {
          kind: "once",
          pattern: "seed-knowledge-starters",
          idempotency: { key: "seed-knowledge-starters.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    ["run", "--lane", "seed", "--dry-run", "--trigger", "manual", "--cwd", dir],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  assert.match(r.stdout, /Ship · seed knowledge starters/);
  const markerPath = path.join(dir, ".ship", "state", "seed-knowledge-starters.v1.json");
  assert.ok(!fs.existsSync(markerPath), "dry-run must not write a marker");
});

test("shipctl run kind=once writes marker and subsequent run is a no-op", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        seed: {
          kind: "once",
          pattern: "seed-knowledge-starters",
          idempotency: {
            key: "seed-knowledge-starters.v1",
            reset_on: "version-change",
          },
        },
      },
    }),
  );
  const first = runCtl(
    ["run", "--lane", "seed", "--trigger", "manual", "--cwd", dir, "--json"],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(first.status, 0, `${first.stdout}\n${first.stderr}`);
  const payload = JSON.parse(first.stdout);
  assert.equal(payload.status, "completed");
  const markerPath = path.join(dir, ".ship", "state", "seed-knowledge-starters.v1.json");
  assert.ok(fs.existsSync(markerPath));
  const marker = JSON.parse(fs.readFileSync(markerPath, "utf8"));
  assert.equal(marker.version, 1);
  assert.equal(marker.lane, "seed");
  assert.equal(marker.pattern_id, "seed-knowledge-starters");
  assert.equal(typeof marker.pattern_sha256, "string");

  const second = runCtl(
    ["run", "--lane", "seed", "--trigger", "manual", "--cwd", dir, "--json"],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(second.status, 0, `${second.stdout}\n${second.stderr}`);
  const second_payload = JSON.parse(second.stdout);
  assert.equal(second_payload.status, "noop");
  assert.equal(second_payload.reason, "already-done");
});

test("shipctl run kind=event exits 0 with not-yet-wired reason", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        pr_review: {
          kind: "event",
          pattern: "catalog-a5-pr-self-review",
          on: "pull_request",
        },
      },
    }),
  );
  const r = runCtl(
    ["run", "--lane", "pr_review", "--trigger", "event", "--cwd", dir, "--json"],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, r.stderr);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.status, "noop");
  assert.match(payload.reason, /not yet wired/);
});

test("shipctl run rejects mismatched trigger for kind=once", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        seed: {
          kind: "once",
          pattern: "seed-knowledge-starters",
          idempotency: { key: "seed-knowledge-starters.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    ["run", "--lane", "seed", "--trigger", "schedule", "--cwd", dir, "--json"],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, r.stderr);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.status, "noop");
  assert.match(payload.reason, /does not accept trigger=schedule/);
});

/* ------------------------------------------------------------------ */
/* idempotency unit tests                                              */
/* ------------------------------------------------------------------ */

test("writeMarker + readMarker round-trip", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  writeMarker(dir, "seed.v1", {
    lane: "seed",
    pattern_id: "seed-x",
    pattern_sha256: "abc",
  });
  const got = readMarker(dir, "seed.v1");
  assert.equal(got.pattern_sha256, "abc");
  assert.equal(got.version, 1);
});

test("decideRun: no marker → run", () => {
  const d = decideRun(null, "pattern body", "version-change");
  assert.equal(d.run, true);
  assert.equal(d.reason, "no-marker");
});

test("decideRun: sha match → no-op", () => {
  const body = "pattern body";
  const marker = { pattern_sha256: sha256(body), version: 1 };
  const d = decideRun(marker, body, "version-change");
  assert.equal(d.run, false);
  assert.equal(d.reason, "already-done");
});

test("decideRun: sha changed + reset_on=version-change → run", () => {
  const marker = { pattern_sha256: "old", version: 1 };
  const d = decideRun(marker, "new body", "version-change");
  assert.equal(d.run, true);
  assert.equal(d.reason, "sha-changed");
});

test("decideRun: sha changed + reset_on=manual → no-op", () => {
  const marker = { pattern_sha256: "old", version: 1 };
  const d = decideRun(marker, "new body", "manual");
  assert.equal(d.run, false);
});

test("resolveMarkerPath rejects invalid keys", () => {
  assert.throws(() => resolveMarkerPath(os.tmpdir(), "has space"), /idempotency key/);
  assert.throws(() => resolveMarkerPath(os.tmpdir(), "UPPER"), /idempotency key/);
});
