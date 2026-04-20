import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  parseCallbackArgs,
  normaliseStatus,
  resolveCallbackUrl,
  buildCallbackBody,
} from "../lib/commands/callback.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

/* IMPORTANT: we use async `spawn` (not `spawnSync`) whenever the child
 * talks to the in-process mock server. `spawnSync` blocks the event loop
 * of the test driver, which means the mock server can never accept the
 * child's POST — the child's fetch hangs forever. The sync helper is
 * kept only for tests that don't hit the network. */
function runCtl(args, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [BIN, ...args], {
      env: { ...process.env, ...env },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("error", reject);
    child.on("close", (status, signal) =>
      resolve({ status, signal, stdout, stderr }),
    );
  });
}

function runCtlSync(args, env = {}) {
  return spawnSync(process.execPath, [BIN, ...args], {
    env: { ...process.env, ...env },
    encoding: "utf8",
  });
}

/* Local HTTP server that mimics Ship's callback endpoint. Captures the
 * request so tests can assert on what shipctl actually sent over the
 * wire — we deliberately do NOT stub fetch directly, because the
 * interesting bugs (auth header shape, URL construction, JSON body
 * framing) all live at the HTTP boundary. */
function startMockShip(handler) {
  return new Promise((resolve) => {
    const received = [];
    const server = http.createServer(async (req, res) => {
      let buf = "";
      req.on("data", (chunk) => (buf += chunk));
      req.on("end", () => {
        let parsed;
        try {
          parsed = buf ? JSON.parse(buf) : null;
        } catch {
          parsed = buf;
        }
        const info = {
          method: req.method,
          url: req.url,
          headers: req.headers,
          body: parsed,
          rawBody: buf,
        };
        received.push(info);
        const reply = handler ? handler(info) : null;
        if (reply && typeof reply === "object" && "status" in reply) {
          res.writeHead(reply.status, {
            "Content-Type": "application/json",
          });
          res.end(reply.body ?? "{}");
          return;
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, received: info.body }));
      });
    });
    server.listen(0, "127.0.0.1", () => {
      const { address, port } = server.address();
      resolve({
        server,
        received,
        url: `http://${address}:${port}`,
        /* Force-close keep-alive sockets; otherwise undici (Node 18+
         * global fetch) holds the connection open and server.close()
         * hangs waiting for it. */
        close: () =>
          new Promise((done) => {
            server.closeAllConnections?.();
            server.close(() => done());
          }),
      });
    });
  });
}

/* ------------------------------------------------------------------ */
/* Unit tests — argv & body shape                                      */
/* ------------------------------------------------------------------ */

test("normaliseStatus accepts documented aliases and rejects garbage", () => {
  assert.equal(normaliseStatus("ok"), "succeeded");
  assert.equal(normaliseStatus("SUCCESS"), "succeeded");
  assert.equal(normaliseStatus("pass"), "succeeded");
  assert.equal(normaliseStatus("fail"), "failed");
  assert.equal(normaliseStatus("failure"), "failed");
  assert.equal(normaliseStatus("cancelled"), "cancelled");
  assert.equal(normaliseStatus("canceled"), "cancelled");
  assert.equal(normaliseStatus("flap"), null);
  assert.equal(normaliseStatus(""), null);
  assert.equal(normaliseStatus(null), null);
});

test("parseCallbackArgs: repeated --metric, summary, status", () => {
  const a = parseCallbackArgs([
    "--status",
    "ok",
    "--summary",
    "Ran clean",
    "--metric",
    "tickets=3",
    "--metric",
    "dry_run=true",
    "--metric",
    "ratio=0.75",
  ]);
  assert.equal(a.status, "ok");
  assert.equal(a.summary, "Ran clean");
  assert.deepEqual(a.metrics, { tickets: 3, dry_run: true, ratio: 0.75 });
});

test("parseCallbackArgs: --metric= form and JSON literal coercion", () => {
  const a = parseCallbackArgs([
    "--status",
    "fail",
    "--metric=tickets_ids=[\"LIN-1\",\"LIN-2\"]",
    "--metric=label=needs-investigation",
  ]);
  assert.deepEqual(a.metrics.tickets_ids, ["LIN-1", "LIN-2"]);
  assert.equal(a.metrics.label, "needs-investigation");
});

test("buildCallbackBody drops empty metrics and trims summary", () => {
  const a = parseCallbackArgs(["--status", "ok"]);
  a.status = "succeeded";
  const body = buildCallbackBody(a);
  assert.deepEqual(body, { status: "succeeded" });

  const long = "x".repeat(2000);
  const a2 = parseCallbackArgs(["--status", "ok", "--summary", long]);
  a2.status = "succeeded";
  const b2 = buildCallbackBody(a2);
  assert.equal(b2.summary.length, 1024);
});

test("resolveCallbackUrl priority: flag > env > base+id", () => {
  const u1 = resolveCallbackUrl(
    { callbackUrl: "https://a/flag", runId: null, baseUrl: null },
    { SHIP_CALLBACK_URL: "https://a/env", SHIP_API_BASE: "https://a/api", SHIP_RUN_ID: "r" },
  );
  assert.equal(u1, "https://a/flag");

  const u2 = resolveCallbackUrl(
    { callbackUrl: null, runId: null, baseUrl: null },
    { SHIP_CALLBACK_URL: "https://a/env" },
  );
  assert.equal(u2, "https://a/env");

  const u3 = resolveCallbackUrl(
    { callbackUrl: null, runId: null, baseUrl: null },
    { SHIP_API_BASE: "https://a/api/", SHIP_RUN_ID: "rid-1" },
  );
  assert.equal(u3, "https://a/api/v1/pipelines/runs/rid-1/result");

  const u4 = resolveCallbackUrl(
    { callbackUrl: null, runId: null, baseUrl: null },
    {},
  );
  assert.equal(u4, null);
});

/* ------------------------------------------------------------------ */
/* E2E tests — full binary against a mock server                       */
/* ------------------------------------------------------------------ */

test("e2e: happy path POSTs expected body with bearer", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--summary",
        "Processed 1 ticket",
        "--metric",
        "tickets=1",
      ],
      {
        SHIP_RUN_TOKEN: "tkn-abc",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-42/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    assert.equal(mock.received.length, 1);
    const got = mock.received[0];
    assert.equal(got.method, "POST");
    assert.equal(got.url, "/v1/pipelines/runs/r-42/result");
    assert.equal(got.headers.authorization, "Bearer tkn-abc");
    assert.match(got.headers["user-agent"] || "", /shipctl/i);
    assert.deepEqual(got.body, {
      status: "succeeded",
      summary: "Processed 1 ticket",
      metrics: { tickets: 1 },
    });
  } finally {
    await mock.close();
  }
});

test("e2e: SHIP_API_BASE + SHIP_RUN_ID builds the URL when no SHIP_CALLBACK_URL", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      ["callback", "--status", "fail"],
      {
        SHIP_RUN_TOKEN: "tkn",
        SHIP_API_BASE: mock.url,
        SHIP_RUN_ID: "rid-xyz",
        /* Explicitly unset in case the process env had it leaking in. */
        SHIP_CALLBACK_URL: "",
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    assert.equal(mock.received[0].url, "/v1/pipelines/runs/rid-xyz/result");
    assert.equal(mock.received[0].body.status, "failed");
  } finally {
    await mock.close();
  }
});

test("e2e: missing SHIP_RUN_TOKEN → exits EXIT_AUTH with clear message", () => {
  const r = runCtlSync(
    ["callback", "--status", "ok", "--callback-url", "http://localhost:1/does-not-matter"],
    { SHIP_RUN_TOKEN: "" },
  );
  assert.notEqual(r.status, 0);
  assert.equal(r.status, 10);
  assert.match(r.stderr, /SHIP_RUN_TOKEN/);
});

test("e2e: unresolvable URL → exits EXIT_CONFIG", () => {
  const r = runCtlSync(["callback", "--status", "ok"], {
    SHIP_RUN_TOKEN: "tkn",
    SHIP_CALLBACK_URL: "",
    SHIP_API_BASE: "",
    SHIP_RUN_ID: "",
  });
  assert.equal(r.status, 11);
  assert.match(r.stderr, /Cannot resolve callback URL/);
});

test("e2e: bad status → exits EXIT_USAGE with help hint", () => {
  const r = runCtlSync(["callback", "--status", "maybe"], {
    SHIP_RUN_TOKEN: "tkn",
    SHIP_CALLBACK_URL: "http://localhost:1/x",
  });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /--status is required/);
});

test("e2e: Ship rejects (422) → non-zero exit with server body surfaced", async () => {
  const mock = await startMockShip(() => ({
    status: 422,
    body: JSON.stringify({ detail: "status must be one of ['cancelled','failed','succeeded']" }),
  }));
  try {
    const r = await runCtl(
      ["callback", "--status", "ok"],
      {
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.notEqual(r.status, 0);
    assert.match(r.stderr, /HTTP 422/);
    assert.match(r.stderr, /status must be one of/);
  } finally {
    await mock.close();
  }
});

test("e2e: --help prints usage and exits 0", () => {
  const r = runCtlSync(["callback", "--help"]);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /shipctl callback/);
  assert.match(r.stdout, /--metric k=v/);
  assert.match(r.stdout, /SHIP_RUN_TOKEN/);
});

test("help output mentions callback command", () => {
  const r = runCtlSync(["help"]);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /shipctl callback/);
});
