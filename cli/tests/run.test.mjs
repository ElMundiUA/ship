import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
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
          pattern: "onboard-seed-knowledge",
          idempotency: { key: "onboard-seed-knowledge.v1" },
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
  const markerPath = path.join(dir, ".ship", "state", "onboard-seed-knowledge.v1.json");
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
          pattern: "onboard-seed-knowledge",
          idempotency: {
            key: "onboard-seed-knowledge.v1",
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
  const markerPath = path.join(dir, ".ship", "state", "onboard-seed-knowledge.v1.json");
  assert.ok(fs.existsSync(markerPath));
  const marker = JSON.parse(fs.readFileSync(markerPath, "utf8"));
  assert.equal(marker.version, 1);
  assert.equal(marker.lane, "seed");
  assert.equal(marker.pattern_id, "onboard-seed-knowledge");
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
          pattern: "flow-pr-self-review",
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
          pattern: "onboard-seed-knowledge",
          idempotency: { key: "onboard-seed-knowledge.v1" },
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

/* ------------------------------------------------------------------ */
/* Callback metrics (RFC-0007 Phase 7B)                                */
/* ------------------------------------------------------------------ */

/* Mock Ship callback endpoint — captures POST bodies so we can assert
 * shipctl decorated the metrics blob with lane + GH run breadcrumbs. */
function startMockShip() {
  return new Promise((resolve) => {
    const received = [];
    const server = http.createServer((req, res) => {
      let buf = "";
      req.on("data", (c) => (buf += c));
      req.on("end", () => {
        let body = null;
        try {
          body = buf ? JSON.parse(buf) : null;
        } catch {
          body = buf;
        }
        received.push({ url: req.url, headers: req.headers, body });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      });
    });
    server.listen(0, "127.0.0.1", () => {
      const { address, port } = server.address();
      resolve({
        server,
        received,
        url: `http://${address}:${port}/v1/pipelines/runs/test/result`,
        close: () =>
          new Promise((done) => {
            server.closeAllConnections?.();
            server.close(() => done());
          }),
      });
    });
  });
}

function runCtlAsync(args, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      env: { ...process.env, SHIP_REPO: REPO_ROOT, ...env },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("error", reject);
    child.on("close", (status) => resolve({ status, stdout, stderr }));
  });
}

test("shipctl run callback includes lane + pattern + GH breadcrumbs", async () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        seed: {
          kind: "once",
          pattern: "onboard-seed-knowledge",
          idempotency: { key: "onboard-seed-knowledge.v1" },
        },
      },
    }),
  );
  const mock = await startMockShip();
  try {
    const r = await runCtlAsync(
      ["run", "--lane", "seed", "--trigger", "manual", "--cwd", dir, "--json"],
      {
        SHIP_CALLBACK_URL: mock.url,
        SHIP_RUN_TOKEN: "test-token",
        GITHUB_RUN_ID: "7777",
        GITHUB_SERVER_URL: "https://github.com",
        GITHUB_REPOSITORY: "acme/widgets",
        GITHUB_EVENT_NAME: "workflow_dispatch",
      },
    );
    assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
    assert.equal(mock.received.length, 1, "expected one callback POST");
    const body = mock.received[0].body;
    assert.equal(body.status, "succeeded");
    assert.ok(body.metrics, "metrics bag should be present");
    assert.equal(body.metrics.lane_id, "seed");
    assert.equal(body.metrics.pattern_id, "onboard-seed-knowledge");
    assert.equal(typeof body.metrics.pattern_sha256, "string");
    assert.equal(body.metrics.gh_workflow_run_id, "7777");
    assert.equal(
      body.metrics.gh_html_url,
      "https://github.com/acme/widgets/actions/runs/7777",
    );
    assert.equal(body.metrics.gh_event, "workflow_dispatch");
  } finally {
    await mock.close();
  }
});

/* ------------------------------------------------------------------ */
/* RFC-0008 C3.2 — multi-pattern lanes                                 */
/* ------------------------------------------------------------------ */

// The monorepo ships these three patterns on disk; we use them because
// `shipctl run` resolves patterns via SHIP_REPO when available, which
// keeps the tests hermetic (no network).
const AUDIT_PATTERNS = [
  "role-tech-architect",
  "role-qa-architect",
  "role-security-officer",
];

test("shipctl run rejects multi-pattern matrix lane without --pattern", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          idempotency: { key: "audit.v1" },
          // fanout defaults to matrix
        },
      },
    }),
  );
  const r = runCtl(
    ["run", "--lane", "audit", "--trigger", "manual", "--cwd", dir],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 1, `${r.stdout}\n${r.stderr}`);
  assert.match(r.stderr, /fanout=matrix/);
  assert.match(r.stderr, /--pattern/);
});

test("shipctl run --pattern executes a single pattern from a matrix lane", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          idempotency: { key: "audit.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    [
      "run",
      "--lane",
      "audit",
      "--pattern",
      "role-qa-architect",
      "--trigger",
      "manual",
      "--cwd",
      dir,
      "--json",
    ],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.status, "completed");
  assert.equal(payload.mode, "single");
  assert.equal(payload.patterns.length, 1);
  assert.equal(payload.patterns[0].id, "role-qa-architect");
});

test("shipctl run --pattern rejects patterns not declared on the lane", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          idempotency: { key: "audit.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    [
      "run",
      "--lane",
      "audit",
      "--pattern",
      "role-intake",
      "--trigger",
      "manual",
      "--cwd",
      dir,
    ],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 1, `${r.stdout}\n${r.stderr}`);
  assert.match(r.stderr, /not declared on lane/);
});

test("shipctl run fanout=sequential emits every pattern's body with a banner", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          fanout: "sequential",
          idempotency: { key: "audit.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    ["run", "--lane", "audit", "--trigger", "manual", "--cwd", dir],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  for (const id of AUDIT_PATTERNS) {
    assert.match(r.stdout, new RegExp(`# ship: pattern=${id} sha256=[0-9a-f]{64}`));
  }
});

test("shipctl run --fanout=sequential overrides a matrix-configured lane", () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          idempotency: { key: "audit.v1" },
        },
      },
    }),
  );
  const r = runCtl(
    [
      "run",
      "--lane",
      "audit",
      "--fanout",
      "sequential",
      "--trigger",
      "manual",
      "--cwd",
      dir,
      "--json",
    ],
    { SHIP_REPO: REPO_ROOT },
  );
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.mode, "sequential");
  assert.equal(payload.patterns.length, AUDIT_PATTERNS.length);
  assert.deepEqual(
    payload.patterns.map((p) => p.id),
    AUDIT_PATTERNS,
  );
});

test("shipctl run multi-pattern callback lists all patterns in metrics", async () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        audit: {
          kind: "once",
          patterns: AUDIT_PATTERNS,
          fanout: "sequential",
          idempotency: { key: "audit.v1" },
        },
      },
    }),
  );
  const mock = await startMockShip();
  try {
    const r = await runCtlAsync(
      ["run", "--lane", "audit", "--trigger", "manual", "--cwd", dir, "--json"],
      { SHIP_CALLBACK_URL: mock.url, SHIP_RUN_TOKEN: "tok" },
    );
    assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
    assert.equal(mock.received.length, 1);
    const body = mock.received[0].body;
    assert.equal(body.status, "succeeded");
    assert.match(body.summary, /3 patterns/);
    assert.equal(body.metrics.lane_id, "audit");
    assert.equal(
      body.metrics.patterns,
      "role-tech-architect,role-qa-architect,role-security-officer",
    );
  } finally {
    await mock.close();
  }
});

test("shipctl run callback omits GH metrics when env is clean", async () => {
  const dir = mktmp();
  writeConfig(
    dir,
    baseConfig({
      lanes: {
        seed: {
          kind: "once",
          pattern: "onboard-seed-knowledge",
          idempotency: { key: "onboard-seed-knowledge.v1" },
        },
      },
    }),
  );
  const mock = await startMockShip();
  try {
    const r = await runCtlAsync(
      ["run", "--lane", "seed", "--trigger", "manual", "--cwd", dir, "--json"],
      {
        SHIP_CALLBACK_URL: mock.url,
        SHIP_RUN_TOKEN: "test-token",
        /* Scrub any inherited GH runner vars the host CI might set. */
        GITHUB_RUN_ID: "",
        GITHUB_SERVER_URL: "",
        GITHUB_REPOSITORY: "",
        GITHUB_EVENT_NAME: "",
      },
    );
    assert.equal(r.status, 0);
    const body = mock.received[0].body;
    assert.equal(body.metrics.lane_id, "seed");
    assert.equal(body.metrics.pattern_id, "onboard-seed-knowledge");
    assert.equal(body.metrics.gh_workflow_run_id, undefined);
    assert.equal(body.metrics.gh_html_url, undefined);
    assert.equal(body.metrics.gh_event, undefined);
  } finally {
    await mock.close();
  }
});
