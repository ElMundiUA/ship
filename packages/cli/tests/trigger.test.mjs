import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  authEnv,
  closeMockServer,
  startMockServer,
  TEST_REPO_ID,
  TEST_TOKEN,
  TEST_WS_ID,
} from "./helpers/mock-workspace-api.mjs";
import { runShipctl } from "./helpers/run-shipctl.mjs";

const ON_THE_HALF = "2026-05-11T15:00:00.000Z";

const DUE_CONFIG = `version: 2
process:
  routines:
    half_hour:
      specialist: developer
      trigger:
        type: schedule
        cron: '*/30 * * * *'
        window: 30m
`;

const NOT_DUE_CONFIG = `version: 2
process:
  routines:
    morning:
      specialist: developer
      trigger:
        type: schedule
        cron: '0 6 * * *'
        window: 30m
`;

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-trigger-"));
}

function seedShipDir(dir, configBody) {
  fs.mkdirSync(path.join(dir, ".ship"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".ship", "config.yml"), configBody);
}

function runCtl(cwd, args, opts = {}) {
  return runShipctl(args, {
    cwd,
    ...opts,
    env: {
      SHIP_API_BASE: "",
      SHIP_WORKSPACE_API_BASE: "",
      ...(opts.env || {}),
    },
  });
}

function triggerRouter(req, res, { workspacesStatus = 200, claimStatus = "claimed" }) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (url.pathname === "/v1/workspaces") {
    res.writeHead(workspacesStatus, { "Content-Type": "application/json" });
    if (workspacesStatus >= 500) {
      res.end(JSON.stringify({ detail: "bad gateway" }));
      return;
    }
    res.end(JSON.stringify([{ id: TEST_WS_ID, name: "Test" }]));
    return;
  }
  if (url.pathname === `/v1/workspaces/${TEST_WS_ID}/repos`) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify([{ id: TEST_REPO_ID, full_name: "org/repo", owner: "org", name: "repo" }]));
    return;
  }
  if (url.pathname.endsWith("/routine-runs/claim") && req.method === "POST") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: claimStatus }));
    return;
  }
  res.writeHead(404);
  res.end();
}

test("trigger: due routine happy path claim POST and routine next_action", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    triggerRouter(req, res, {}),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      { env: { SHIP_WORKSPACE_ID: TEST_WS_ID } },
    );
    assert.equal(r.status, 0, r.stderr);
    const claim = requests.find(
      (q) => q.method === "POST" && q.url.includes("/routine-runs/claim"),
    );
    assert.ok(claim, "expected claim POST");
    assert.equal(claim.body.routine_id, "half_hour");
    const out = JSON.parse(r.stdout);
    assert.equal(out.claim_status, "attempted");
    assert.equal(out.next_action.kind, "routine");
    assert.equal(out.next_action.routine_id, "half_hour");
    assert.ok(out.due_routines.some((d) => d.claim_status === "claimed"));
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: SHIP_WORKSPACE_ID skips GET /v1/workspaces", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    triggerRouter(req, res, {}),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      { env: { SHIP_WORKSPACE_ID: TEST_WS_ID } },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.equal(requests.some((q) => q.url === "/v1/workspaces"), false);
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: no SHIP_API_TOKEN skips claim without failing", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    triggerRouter(req, res, {}),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      { minimalEnv: true, env: { SHIP_API_TOKEN: "", SHIP_WORKSPACE_ID: "" } },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.equal(requests.length, 0);
    const out = JSON.parse(r.stdout);
    assert.equal(out.claim_status, "skipped:no-token");
    assert.equal(out.due_routines.length, 1);
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: token but no due routines makes no claim HTTP calls", async () => {
  const dir = mktmp();
  seedShipDir(dir, NOT_DUE_CONFIG);
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    triggerRouter(req, res, {}),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      { env: { SHIP_WORKSPACE_ID: TEST_WS_ID } },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.equal(
      requests.some((q) => q.url.includes("/routine-runs/claim")),
      false,
    );
    const out = JSON.parse(r.stdout);
    assert.equal(out.next_action.kind, "noop");
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: transient 502 on workspaces is noop edge_unavailable", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl } = await startMockServer((req, res) =>
    triggerRouter(req, res, { workspacesStatus: 502 }),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      {
        minimalEnv: true,
        env: { SHIP_API_TOKEN: TEST_TOKEN, SHIP_WORKSPACE_ID: "" },
      },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stderr, /edge transient|502/i);
    const out = JSON.parse(r.stdout);
    assert.equal(out.claim_status, "skipped:edge_unavailable");
    assert.equal(out.next_action.kind, "noop");
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: transient 503 on workspaces is noop edge_unavailable", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl } = await startMockServer((req, res) =>
    triggerRouter(req, res, { workspacesStatus: 503 }),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      {
        minimalEnv: true,
        env: { SHIP_API_TOKEN: TEST_TOKEN, SHIP_WORKSPACE_ID: "" },
      },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stderr, /edge transient|503/i);
    const out = JSON.parse(r.stdout);
    assert.equal(out.claim_status, "skipped:edge_unavailable");
    assert.equal(out.next_action.kind, "noop");
  } finally {
    await closeMockServer(server);
  }
});

test("trigger: transient 503 on workspaces is noop edge_unavailable", async () => {
  const dir = mktmp();
  seedShipDir(dir, DUE_CONFIG);
  const { server, baseUrl } = await startMockServer((req, res) =>
    triggerRouter(req, res, { workspacesStatus: 503 }),
  );
  try {
    const r = await runCtl(
      dir,
      [
        "--base-url",
        baseUrl,
        "trigger",
        "--event",
        "schedule",
        "--cwd",
        dir,
        "--now",
        ON_THE_HALF,
        "--json",
      ],
      {
        minimalEnv: true,
        env: { SHIP_API_TOKEN: TEST_TOKEN, SHIP_WORKSPACE_ID: "" },
      },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stderr, /edge transient|503/i);
    const out = JSON.parse(r.stdout);
    assert.equal(out.claim_status, "skipped:edge_unavailable");
    assert.equal(out.next_action.kind, "noop");
  } finally {
    await closeMockServer(server);
  }
});
