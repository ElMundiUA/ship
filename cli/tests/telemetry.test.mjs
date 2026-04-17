import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";

import {
  outboxPath,
  appendEvent,
  listEvents,
  countEvents,
  clearEvents,
  writeAllEvents,
} from "../lib/telemetry/outbox.mjs";
import { readConfig, writeConfig } from "../lib/config/io.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-tel-"));
}

function runCtl(args, { cwd, env, input } = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd: cwd || process.cwd(),
    env: { ...process.env, ...(env || {}) },
    encoding: "utf8",
    input,
  });
}

function runCtlAsync(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      cwd: opts.cwd,
      env: { ...process.env, ...(opts.env || {}) },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("error", reject);
    child.on("close", (code) => resolve({ status: code, stdout, stderr }));
  });
}

function initRepo(dir, { share = false } = {}) {
  spawnSync("git", ["init", "-q"], { cwd: dir });
  const r = runCtl(["config", "init", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  if (share) {
    const { config, filePath } = readConfig(dir);
    config.telemetry.share = true;
    writeConfig(filePath, config);
  }
  return dir;
}

test("outbox: append 3 events, list returns 3, clear removes all", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const { config } = readConfig(dir);
  const anon = config.telemetry.anonymous_id;

  for (let i = 0; i < 3; i++) {
    appendEvent(dir, {
      type: "artifact.fetch",
      anonymous_id: anon,
      payload: { kind: "pattern", id: `p${i}`, version: "1.0.0", source: "cache", ttl_age_h: 0 },
    });
  }

  assert.equal(countEvents(dir), 3);
  const events = listEvents(dir);
  assert.equal(events.length, 3);
  assert.equal(events[0].type, "artifact.fetch");

  const removed = clearEvents(dir);
  assert.equal(removed, 3);
  assert.equal(countEvents(dir), 0);
  assert.equal(fs.existsSync(outboxPath(dir)), false);
});

test("outbox: denylisted key (path) is stripped", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const { config } = readConfig(dir);
  appendEvent(dir, {
    type: "artifact.fetch",
    anonymous_id: config.telemetry.anonymous_id,
    payload: {
      kind: "pattern",
      id: "cloud-developer",
      version: "1.0.0",
      source: "cache",
      ttl_age_h: 0,
      path: "/Users/secret/file.ts",
      nested: { branch: "main", ok: true },
    },
  });
  const [ev] = listEvents(dir);
  assert.ok(!("path" in ev.payload), "path should be stripped");
  assert.ok(!("branch" in ev.payload.nested), "nested branch should be stripped");
  assert.equal(ev.payload.nested.ok, true);
});

test("outbox: unknown event type throws", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  assert.throws(() =>
    appendEvent(dir, {
      type: "bogus.event",
      anonymous_id: "00000000-0000-4000-8000-000000000000",
      payload: {},
    }),
  );
});

test("telemetry off: subsequent appendEvent is a no-op", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const before = readConfig(dir).config.telemetry.anonymous_id;

  const off = runCtl(["telemetry", "off", "--cwd", dir]);
  assert.equal(off.status, 0, off.stderr);

  const ok = appendEvent(dir, {
    type: "artifact.use",
    anonymous_id: before,
    payload: { kind: "pattern", id: "x", version: "1.0.0", agent: "cursor" },
  });
  assert.equal(ok, false);
  assert.equal(countEvents(dir), 0);
});

test("telemetry status output contains anonymous_id and outbox_pending", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const { config } = readConfig(dir);
  appendEvent(dir, {
    type: "artifact.sync",
    anonymous_id: config.telemetry.anonymous_id,
    payload: { categories: ["pattern"], updates_count: 1, failures_count: 0 },
  });

  const r = runCtl(["telemetry", "status", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /anonymous_id=/);
  assert.match(r.stdout, /outbox_pending=1/);
  assert.match(r.stdout, /share=true/);
});

test("telemetry reset-id changes the anonymous_id", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const beforeId = readConfig(dir).config.telemetry.anonymous_id;

  const r = runCtl(["telemetry", "reset-id", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  const newId = readConfig(dir).config.telemetry.anonymous_id;
  assert.notEqual(newId, beforeId);
  assert.ok(/^[0-9a-f-]{36}$/.test(newId));
});

function startServer({ status = 202, response = { accepted: 0, rejected: 0 } } = {}) {
  let received = null;
  const server = http.createServer((req, res) => {
    let chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      try {
        received = JSON.parse(body);
      } catch {
        received = body;
      }
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(response));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({
        server,
        baseUrl: `http://127.0.0.1:${addr.port}`,
        getReceived: () => received,
      });
    });
  });
}

test("telemetry flush: server accepts → outbox empty after", async () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const { config } = readConfig(dir);
  for (let i = 0; i < 3; i++) {
    appendEvent(dir, {
      type: "artifact.fetch",
      anonymous_id: config.telemetry.anonymous_id,
      payload: { kind: "pattern", id: `p${i}`, version: "1.0.0", source: "network", ttl_age_h: 0 },
    });
  }
  assert.equal(countEvents(dir), 3);

  const { server, baseUrl, getReceived } = await startServer({
    status: 202,
    response: { accepted: 3, rejected: 0, reasons: [] },
  });
  try {
    const r = await runCtlAsync(
      ["--base-url", baseUrl, "telemetry", "flush", "--cwd", dir],
    );
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /flushed 3 events, 0 failed/);
    assert.equal(countEvents(dir), 0);
    const sent = getReceived();
    assert.equal(sent.events.length, 3);
    assert.equal(sent.events[0].type, "artifact.fetch");
    assert.ok(sent.events[0].timestamp);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("telemetry flush: server 500 → events preserved", async () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const { config } = readConfig(dir);
  appendEvent(dir, {
    type: "artifact.fetch",
    anonymous_id: config.telemetry.anonymous_id,
    payload: { kind: "pattern", id: "p", version: "1.0.0", source: "cache", ttl_age_h: 0 },
  });
  assert.equal(countEvents(dir), 1);

  const { server, baseUrl } = await startServer({
    status: 500,
    response: { error: "boom" },
  });
  try {
    const r = await runCtlAsync(
      ["--base-url", baseUrl, "telemetry", "flush", "--cwd", dir],
    );
    assert.notEqual(r.status, 0);
    assert.match(r.stdout, /flushed 0 events, 1 failed/);
    assert.equal(countEvents(dir), 1);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("telemetry flush: disabled → nothing to send, exits 0", () => {
  const dir = mktmp();
  initRepo(dir, { share: false });
  const r = runCtl(["telemetry", "flush", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /telemetry disabled/);
});

test("outbox: old-shape ({event,ts}) events are upgraded on read", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const file = outboxPath(dir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const legacy = {
    event: "artifact.sync",
    ts: "2026-04-17T10:00:00Z",
    anonymous_id: readConfig(dir).config.telemetry.anonymous_id,
    shipctl_version: "0.9.0",
    payload: { categories: ["pattern"], updates_count: 1, failures_count: 0 },
  };
  fs.writeFileSync(file, JSON.stringify(legacy) + "\n", "utf8");

  const [ev] = listEvents(dir);
  assert.equal(ev.type, "artifact.sync");
  assert.equal(ev.timestamp, "2026-04-17T10:00:00Z");
  assert.equal(ev.payload.updates_count, 1);
});

test("writeAllEvents round-trips", () => {
  const dir = mktmp();
  initRepo(dir, { share: true });
  const events = [
    {
      type: "artifact.fetch",
      anonymous_id: "00000000-0000-4000-8000-000000000001",
      timestamp: "2026-04-17T10:00:00Z",
      payload: { kind: "pattern", id: "p", version: "1.0.0", source: "cache", ttl_age_h: 0 },
    },
  ];
  writeAllEvents(dir, events);
  assert.equal(countEvents(dir), 1);
  writeAllEvents(dir, []);
  assert.equal(countEvents(dir), 0);
});
