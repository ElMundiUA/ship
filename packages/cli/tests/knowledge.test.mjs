import { test } from "node:test";
import assert from "node:assert/strict";
import {
  closeMockServer,
  startMockServer,
  TEST_TOKEN,
  TEST_WS_ID,
} from "./helpers/mock-workspace-api.mjs";
import { runShipctl } from "./helpers/run-shipctl.mjs";

const BUCKET = "developer";

function knowledgeRouter(req, res, { bucketStatus = 200, multiWorkspace = false }) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (url.pathname === "/v1/workspaces") {
    res.writeHead(200, { "Content-Type": "application/json" });
    if (multiWorkspace) {
      res.end(
        JSON.stringify([
          { id: "ws-a", name: "A" },
          { id: "ws-b", name: "B" },
        ]),
      );
      return;
    }
    res.end(JSON.stringify([{ id: TEST_WS_ID, name: "Test" }]));
    return;
  }
  const bucketPath = `/v1/workspaces/${TEST_WS_ID}/buckets/${BUCKET}`;
  if (url.pathname === bucketPath && req.method === "GET") {
    res.writeHead(bucketStatus, { "Content-Type": "application/json" });
    if (bucketStatus === 404) {
      res.end(JSON.stringify({ detail: "not found" }));
      return;
    }
    res.end(JSON.stringify({ name: "Developer", slug: BUCKET, scope_kind: "role", source_kind: "seed" }));
    return;
  }
  if (url.pathname === `${bucketPath}/articles`) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify([{ title: "A", slug: "a", body_md: "# A" }]));
    return;
  }
  if (url.pathname === `${bucketPath}/sources`) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify([]));
    return;
  }
  res.writeHead(404);
  res.end();
}

test("knowledge fetch: happy path parallel GETs and --json shape", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    knowledgeRouter(req, res, {}),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "knowledge",
      "fetch",
      BUCKET,
      "--json",
    ]);
    assert.equal(r.status, 0, r.stderr);
    const gets = requests.filter((q) => q.method === "GET");
    const paths = gets.map((q) => q.url);
    assert.ok(paths.some((p) => p.includes(`/buckets/${BUCKET}`) && !p.includes("/articles")));
    assert.ok(paths.some((p) => p.endsWith("/articles")));
    assert.ok(paths.some((p) => p.endsWith("/sources")));
    const out = JSON.parse(r.stdout);
    assert.equal(out.bucket.slug, BUCKET);
    assert.ok(Array.isArray(out.articles));
    assert.ok(Array.isArray(out.sources));
  } finally {
    await closeMockServer(server);
  }
});

test("knowledge fetch: 404 on bucket exits 1", async () => {
  const { server, baseUrl } = await startMockServer((req, res) =>
    knowledgeRouter(req, res, { bucketStatus: 404 }),
  );
  try {
    const r = await runShipctl(["--base-url", baseUrl, "knowledge", "fetch", BUCKET]);
    assert.equal(r.status, 1, r.stderr);
    assert.match(r.stderr, /404/);
  } finally {
    await closeMockServer(server);
  }
});

test("knowledge fetch: missing bucket slug exits 1 before network", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    knowledgeRouter(req, res, {}),
  );
  try {
    const r = await runShipctl(["--base-url", baseUrl, "knowledge", "fetch"], {
      SHIP_API_TOKEN: TEST_TOKEN,
      SHIP_WORKSPACE_ID: TEST_WS_ID,
    });
    assert.equal(r.status, 1);
    assert.match(r.stderr, /Usage: shipctl knowledge fetch/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("knowledge fetch: --base-url beats SHIP_WORKSPACE_API_BASE and SHIP_API_BASE", async () => {
  const flagPort = await startMockServer((req, res) => knowledgeRouter(req, res, {}));
  const envPort = await startMockServer((req, res) => {
    res.writeHead(500);
    res.end("env-server");
  });
  const genericPort = await startMockServer((req, res) => {
    res.writeHead(500);
    res.end("generic-server");
  });
  try {
    const r = await runShipctl(
      ["--base-url", flagPort.baseUrl, "knowledge", "fetch", BUCKET, "--json"],
      {
        SHIP_WORKSPACE_API_BASE: envPort.baseUrl,
        SHIP_API_BASE: genericPort.baseUrl,
      },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.ok(flagPort.requests.length > 0);
    assert.equal(envPort.requests.length, 0);
    assert.equal(genericPort.requests.length, 0);
  } finally {
    await closeMockServer(flagPort.server);
    await closeMockServer(envPort.server);
    await closeMockServer(genericPort.server);
  }
});

test("knowledge fetch: SHIP_WORKSPACE_ID skips GET /v1/workspaces", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    knowledgeRouter(req, res, {}),
  );
  try {
    const r = await runShipctl(["--base-url", baseUrl, "knowledge", "fetch", BUCKET, "--json"], {
      SHIP_WORKSPACE_ID: TEST_WS_ID,
    });
    assert.equal(r.status, 0, r.stderr);
    assert.equal(
      requests.some((q) => q.url === "/v1/workspaces"),
      false,
    );
  } finally {
    await closeMockServer(server);
  }
});

test("knowledge fetch: multi-workspace token without --workspace exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res) =>
    knowledgeRouter(req, res, { multiWorkspace: true }),
  );
  try {
    const r = await runShipctl(["--base-url", baseUrl, "knowledge", "fetch", BUCKET], {
      minimalEnv: true,
      env: { SHIP_WORKSPACE_ID: "" },
    });
    assert.equal(r.status, 1);
    assert.match(r.stderr, /more than one workspace/);
    assert.ok(requests.some((q) => q.url === "/v1/workspaces"));
  } finally {
    await closeMockServer(server);
  }
});

test("knowledge fetch: --workspace flag overrides env", async () => {
  const altWs = "dddddddd-dddd-4ddd-dddd-dddddddddddd";
  const { server, baseUrl, requests } = await startMockServer((req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname === `/v1/workspaces/${altWs}/buckets/${BUCKET}`) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ name: "Alt", slug: BUCKET, scope_kind: "x", source_kind: "y" }));
      return;
    }
    if (url.pathname.endsWith("/articles") || url.pathname.endsWith("/sources")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify([]));
      return;
    }
    res.writeHead(404);
    res.end();
  });
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "knowledge",
        "fetch",
        BUCKET,
        "--workspace",
        altWs,
        "--json",
      ],
      { SHIP_WORKSPACE_ID: TEST_WS_ID },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.ok(
      requests.some((q) => q.url.includes(encodeURIComponent(altWs))),
    );
    assert.equal(requests.some((q) => q.url === "/v1/workspaces"), false);
  } finally {
    await closeMockServer(server);
  }
});
