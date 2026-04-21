import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import YAML from "yaml";

import {
  entryFromBody,
  lockfilePath,
  lookupLock,
  readLockfile,
  verifyBody,
  writeLockfile,
  LOCKFILE_SCHEMA_VERSION,
} from "../lib/state/lockfile.mjs";
import { artifactSha256 } from "../lib/cache/store.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

/* The sync+run tests below reuse the Ship monorepo as a pattern source
 * so they don't need the methodology API to respond; `SHIP_REPO` points
 * the runtime at the checked-out tree, and the `seed-knowledge-starters`
 * pattern we committed in Phase 2 supplies the body. */
const REPO_ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-lockfile-"));
}

function seedRepo(dir, extraLanes = {}) {
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    YAML.stringify({
      version: 2,
      shipctl_min: "0.12.0",
      api: { base_url: "https://ship.example.com", channel: "stable" },
      stack: { tracker: "none", ci: "manual", preset: "adoption-minimum", language: "multi" },
      agent: { default: { provider: null }, overrides: {} },
      lanes: {
        seed_knowledge_starters: {
          kind: "once",
          pattern: "seed-knowledge-starters",
          idempotency: {
            key: "seed-knowledge-starters.v1",
            store: "file",
            reset_on: "version-change",
          },
        },
        ...extraLanes,
      },
      artifacts: { pins: {}, auto_update: true },
      cache: { vcs_tracked: false },
      telemetry: { share: false, anonymous_id: null, scope: { artifact_usage: true } },
    }),
    "utf8",
  );
  return { dir };
}

function runCtl(cwd, args, env = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, SHIP_REPO: REPO_ROOT, ...env },
  });
}

/* ─────────────────────────── lockfile module ─────────────────────────── */

test("writeLockfile / readLockfile round-trip", () => {
  const root = mktmp();
  fs.mkdirSync(path.join(root, ".ship"), { recursive: true });
  const entry = entryFromBody({
    body: "hello world\n",
    version: "1.0.0",
    cachedPath: ".ship/cache/pattern/x@1.0.0/ARTIFACT.md",
    source: "monorepo",
    pinned: false,
  });
  const data = {
    version: LOCKFILE_SCHEMA_VERSION,
    generated_at: "2026-04-21T00:00:00Z",
    shipctl_version: "0.12.0",
    source: { base_url: "https://x", channel: "stable" },
    artifacts: { "pattern/x": entry },
  };
  writeLockfile(root, data);
  const round = readLockfile(root);
  assert.equal(round.version, 1);
  assert.equal(round.artifacts["pattern/x"].content_sha256, artifactSha256("hello world\n"));
  assert.deepEqual(lookupLock(round, "pattern", "x"), entry);
});

test("readLockfile rejects unknown schema version", () => {
  const root = mktmp();
  fs.mkdirSync(path.join(root, ".ship"), { recursive: true });
  fs.writeFileSync(
    lockfilePath(root),
    JSON.stringify({ version: 99, artifacts: {} }),
    "utf8",
  );
  assert.throws(() => readLockfile(root), /unsupported version 99/);
});

test("verifyBody detects sha drift", () => {
  const entry = entryFromBody({
    body: "a\n",
    version: "1.0.0",
    cachedPath: "x",
    source: "monorepo",
  });
  assert.equal(verifyBody(entry, "a\n").ok, true);
  const diff = verifyBody(entry, "b\n");
  assert.equal(diff.ok, false);
  assert.equal(diff.reason, "sha-mismatch");
});

test("writeLockfile sorts artifact keys for stable diffs", () => {
  const root = mktmp();
  fs.mkdirSync(path.join(root, ".ship"), { recursive: true });
  const entry = (body) =>
    entryFromBody({ body, version: "1.0.0", cachedPath: "x", source: "monorepo" });
  writeLockfile(root, {
    version: LOCKFILE_SCHEMA_VERSION,
    generated_at: "t",
    shipctl_version: "0.12.0",
    source: null,
    artifacts: {
      "pattern/z": entry("z"),
      "pattern/a": entry("a"),
      "pattern/m": entry("m"),
    },
  });
  const raw = fs.readFileSync(lockfilePath(root), "utf8");
  const aIdx = raw.indexOf('"pattern/a"');
  const mIdx = raw.indexOf('"pattern/m"');
  const zIdx = raw.indexOf('"pattern/z"');
  assert.ok(aIdx > 0 && mIdx > aIdx && zIdx > mIdx, "keys must appear in lexicographic order");
});

/* ───────────────────────── shipctl sync --lock ───────────────────────── */

test("shipctl sync --lock produces a lockfile covering declared lane patterns", () => {
  const { dir } = seedRepo(mktmp());
  const res = runCtl(dir, ["sync", "--lock", "--json"]);
  assert.equal(res.status, 0, `stderr: ${res.stderr}`);
  const summary = JSON.parse(res.stdout);
  assert.ok(summary.lock, "summary includes lock section");
  assert.ok(summary.lock.entries >= 1);
  assert.equal(summary.lock.unresolved.length, 0);

  const lock = readLockfile(dir);
  assert.ok(lock, "lockfile exists on disk");
  const entry = lookupLock(lock, "pattern", "seed-knowledge-starters");
  assert.ok(entry, "seed-knowledge-starters recorded");
  assert.match(entry.content_sha256, /^[0-9a-f]{64}$/);
  assert.equal(entry.source, "monorepo");
});

test("shipctl sync --lock surfaces unresolved patterns", () => {
  const { dir } = seedRepo(mktmp(), {
    ghost_lane: {
      kind: "once",
      pattern: "this-pattern-does-not-exist",
      idempotency: { key: "ghost.v1", store: "file", reset_on: "version-change" },
    },
  });
  /* Force an environment where the resolver can't fall back to a monorepo
   * (SHIP_REPO unset → no local artifacts) and can't reach the API
   * either (invalid base_url). */
  const res = runCtl(
    dir,
    ["sync", "--lock", "--json"],
    { SHIP_REPO: "", SHIP_API_BASE: "http://127.0.0.1:1" },
  );
  assert.notEqual(res.status, 0, "non-zero when any pattern is unresolved");
  /* The sync command prints the error to stderr and still emits JSON to
   * stdout when --json is active (fail-with-context). */
  assert.match(res.stderr + res.stdout, /unresolved/);
});

/* ────────────────────────── shipctl run --offline ─────────────────────── */

test("shipctl run --offline reads patterns from the lockfile + cache", () => {
  const { dir } = seedRepo(mktmp());
  /* Prime the cache and lockfile. */
  assert.equal(runCtl(dir, ["sync", "--lock", "--json"]).status, 0);

  /* Run offline — SHIP_API_BASE is bogus on purpose to prove there's no
   * network call on the happy path. */
  const res = runCtl(
    dir,
    ["run", "--lane", "seed_knowledge_starters", "--trigger", "manual", "--offline", "--json"],
    { SHIP_API_BASE: "http://127.0.0.1:1" },
  );
  assert.equal(res.status, 0, `stderr: ${res.stderr}`);
  const summary = JSON.parse(res.stdout);
  assert.equal(summary.status, "completed");
  assert.equal(summary.pattern.source, "lockfile");
});

test("shipctl run --offline fails when the lockfile is absent", () => {
  const { dir } = seedRepo(mktmp());
  const res = runCtl(
    dir,
    ["run", "--lane", "seed_knowledge_starters", "--trigger", "manual", "--offline", "--json"],
    { SHIP_API_BASE: "http://127.0.0.1:1" },
  );
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /shipctl sync --lock/);
});

test("shipctl run --offline rejects a cache body that drifts from the lock", () => {
  const { dir } = seedRepo(mktmp());
  assert.equal(runCtl(dir, ["sync", "--lock", "--json"]).status, 0);
  const lock = readLockfile(dir);
  const entry = lookupLock(lock, "pattern", "seed-knowledge-starters");
  const abs = path.join(dir, entry.cached_path);
  /* Corrupt the cached body — should trip the sha mismatch. */
  fs.appendFileSync(abs, "\n# tampered\n");

  const res = runCtl(
    dir,
    ["run", "--lane", "seed_knowledge_starters", "--trigger", "manual", "--offline", "--json"],
    { SHIP_API_BASE: "http://127.0.0.1:1" },
  );
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /sha256 mismatch/);
});

test("shipctl run online warns when the pattern drifts from the lockfile", () => {
  const { dir } = seedRepo(mktmp());
  assert.equal(runCtl(dir, ["sync", "--lock", "--json"]).status, 0);

  /* Corrupt the lockfile entry (swap the sha) — the online resolver
   * reads the pattern from the monorepo, then verifies it against the
   * lockfile and should emit a warning (but still succeed). */
  const file = lockfilePath(dir);
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  data.artifacts["pattern/seed-knowledge-starters"].content_sha256 = "0".repeat(64);
  fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf8");

  const res = runCtl(
    dir,
    ["run", "--lane", "seed_knowledge_starters", "--trigger", "manual", "--json"],
  );
  assert.equal(res.status, 0, `stderr: ${res.stderr}`);
  assert.match(res.stderr, /sha256 drift vs lockfile/);
});
