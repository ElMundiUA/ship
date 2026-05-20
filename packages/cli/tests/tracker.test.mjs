import { test } from "node:test";
import assert from "node:assert/strict";
import {
  closeMockServer,
  startMockServer,
  TEST_TOKEN,
  TEST_WS_ID,
} from "./helpers/mock-workspace-api.mjs";
import { runShipctl } from "./helpers/run-shipctl.mjs";

const PROJECT_ID = "cccccccc-cccc-4ccc-dddd-cccccccccccc";

function trackerRouter(req, res, { body }, statusByPath = {}) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  const status = statusByPath[url.pathname] ?? 200;
  if (status === 401) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: "unauthorized" }));
    return;
  }
  if (status >= 500) {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: "server error" }));
    return;
  }
  if (req.method === "POST" && url.pathname.endsWith("/tracker/tickets")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        ticket_ref: "ELS-1",
        url: "https://example.test/ELS-1",
        received: body,
      }),
    );
    return;
  }
  if (req.method === "GET" && url.pathname.includes("/tracker/projects/")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        tickets: [
          {
            ticket_ref: "ELS-1",
            state: "Todo",
            labels: ["bug"],
            title: "Example",
          },
        ],
      }),
    );
    return;
  }
  res.writeHead(404);
  res.end();
}

test("tracker create-ticket: happy path POST body and exit 0", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "create-ticket",
      "--project-id",
      PROJECT_ID,
      "--title",
      "Test ticket",
      "--body",
      "Body markdown",
      "--labels",
      "a,b",
      "--priority",
      "2",
      "--json",
    ]);
    assert.equal(r.status, 0, r.stderr);
    const post = requests.find((q) => q.method === "POST");
    assert.ok(post, "expected POST");
    assert.match(post.url, /\/tracker\/tickets$/);
    assert.equal(post.headers.authorization, `Bearer ${TEST_TOKEN}`);
    assert.equal(post.body.project_id, PROJECT_ID);
    assert.equal(post.body.title, "Test ticket");
    assert.equal(post.body.body, "Body markdown");
    assert.deepEqual(post.body.labels, ["a", "b"]);
    assert.equal(post.body.priority, 2);
    const out = JSON.parse(r.stdout);
    assert.equal(out.ticket_ref, "ELS-1");
  } finally {
    await closeMockServer(server);
  }
});

test("tracker create-ticket: missing --project-id exits 1 without HTTP", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "tracker",
        "create-ticket",
        "--title",
        "T",
        "--body",
        "b",
      ],
      { SHIP_API_TOKEN: "" },
    );
    assert.equal(r.status, 1);
    assert.match(r.stderr, /--project-id is required/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("tracker create-ticket: missing body exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "create-ticket",
      "--project-id",
      PROJECT_ID,
      "--title",
      "T",
    ]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /Pass --body .* or --body-file/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("tracker create-ticket: HTTP 401 exits 2", async () => {
  const ticketPath = `/v1/workspaces/${TEST_WS_ID}/tracker/tickets`;
  const { server, baseUrl } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, { [ticketPath]: 401 }),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "create-ticket",
      "--project-id",
      PROJECT_ID,
      "--title",
      "T",
      "--body",
      "b",
    ]);
    assert.equal(r.status, 2, r.stderr);
    assert.match(r.stderr, /401/);
  } finally {
    await closeMockServer(server);
  }
});

test("tracker create-ticket: HTTP 500 exits 3", async () => {
  const ticketPath = `/v1/workspaces/${TEST_WS_ID}/tracker/tickets`;
  const { server, baseUrl } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, { [ticketPath]: 500 }),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "create-ticket",
      "--project-id",
      PROJECT_ID,
      "--title",
      "T",
      "--body",
      "b",
    ]);
    assert.equal(r.status, 3, r.stderr);
    assert.match(r.stderr, /500/);
  } finally {
    await closeMockServer(server);
  }
});

test("tracker list-project-tickets: default open_only=true and happy GET", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "list-project-tickets",
      "--project-id",
      PROJECT_ID,
      "--json",
    ]);
    assert.equal(r.status, 0, r.stderr);
    const get = requests.find((q) => q.method === "GET");
    assert.ok(get);
    const url = new URL(get.url, baseUrl);
    assert.equal(url.searchParams.get("open_only"), "true");
    const out = JSON.parse(r.stdout);
    assert.equal(out.tickets.length, 1);
  } finally {
    await closeMockServer(server);
  }
});

test("tracker list-project-tickets: --open-only false", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "list-project-tickets",
      "--project-id",
      PROJECT_ID,
      "--open-only",
      "false",
    ]);
    assert.equal(r.status, 0, r.stderr);
    const get = requests.find((q) => q.method === "GET");
    const url = new URL(get.url, baseUrl);
    assert.equal(url.searchParams.get("open_only"), "false");
  } finally {
    await closeMockServer(server);
  }
});

test("tracker list-project-tickets: --limit 0 uses fallback 100; --limit 999 clamped to 250", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r0 = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "list-project-tickets",
      "--project-id",
      PROJECT_ID,
      "--limit",
      "0",
    ]);
    assert.equal(r0.status, 0, r0.stderr);
    const url0 = new URL(requests.at(-1).url, baseUrl);
    assert.equal(url0.searchParams.get("limit"), "100");

    const r999 = await runShipctl([
      "--base-url",
      baseUrl,
      "tracker",
      "list-project-tickets",
      "--project-id",
      PROJECT_ID,
      "--limit",
      "999",
    ]);
    assert.equal(r999.status, 0, r999.stderr);
    const url999 = new URL(requests.at(-1).url, baseUrl);
    assert.equal(url999.searchParams.get("limit"), "250");
  } finally {
    await closeMockServer(server);
  }
});

test("tracker create-ticket: missing SHIP_API_TOKEN exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    trackerRouter(req, res, ctx, {}),
  );
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "tracker",
        "create-ticket",
        "--project-id",
        PROJECT_ID,
        "--title",
        "T",
        "--body",
        "b",
      ],
      { minimalEnv: true, env: { SHIP_API_TOKEN: "", SHIP_WORKSPACE_ID: "" } },
    );
    assert.equal(r.status, 1);
    assert.match(r.stderr, /SHIP_API_TOKEN is required/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});
