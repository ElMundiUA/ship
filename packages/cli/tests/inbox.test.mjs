import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { closeMockServer, startMockServer } from "./helpers/mock-workspace-api.mjs";
import { runShipctl } from "./helpers/run-shipctl.mjs";

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-inbox-"));
}

function inboxRouter(req, res, { body }) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (req.method === "POST" && url.pathname.endsWith("/inbox/items")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ id: "letter-1", received: body }));
    return;
  }
  res.writeHead(404);
  res.end();
}

const INBOX_TYPES = ["report", "improvement", "approval", "exception"];

for (const type of INBOX_TYPES) {
  test(`inbox create: type=${type} POST body`, async () => {
    const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
      inboxRouter(req, res, ctx),
    );
    try {
      const r = await runShipctl([
        "--base-url",
        baseUrl,
        "inbox",
        "create",
        "--type",
        type,
        "--title",
        `Title for ${type}`,
        "--body",
        "Letter body",
        "--json",
      ]);
      assert.equal(r.status, 0, r.stderr);
      const post = requests.find((q) => q.method === "POST");
      assert.equal(post.body.type, type);
      assert.equal(post.body.title, `Title for ${type}`);
      assert.equal(post.body.body, "Letter body");
    } finally {
      await closeMockServer(server);
    }
  });
}

test("inbox create: --body inline", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "inbox",
      "create",
      "--type",
      "report",
      "--title",
      "Inline",
      "--body",
      "inline markdown",
    ]);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(requests[0].body.body, "inline markdown");
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: --body-file path", async () => {
  const dir = mktmp();
  const bodyPath = path.join(dir, "body.md");
  fs.writeFileSync(bodyPath, "from file\n", "utf8");
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "inbox",
      "create",
      "--type",
      "report",
      "--title",
      "File body",
      "--body-file",
      bodyPath,
    ]);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(requests[0].body.body.trim(), "from file");
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: --body-file=- reads stdin", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "inbox",
        "create",
        "--type",
        "report",
        "--title",
        "Stdin",
        "--body-file",
        "-",
      ],
      { input: "stdin body content" },
    );
    assert.equal(r.status, 0, r.stderr);
    assert.equal(requests[0].body.body, "stdin body content");
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: --body and --body-file mutual exclusion exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "inbox",
      "create",
      "--type",
      "report",
      "--title",
      "T",
      "--body",
      "a",
      "--body-file",
      "-",
    ]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /Pass --body OR --body-file, not both/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: missing --type exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "inbox",
      "create",
      "--title",
      "T",
      "--body",
      "b",
    ]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /--type must be one of/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: missing --title exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "inbox",
      "create",
      "--type",
      "report",
      "--body",
      "b",
    ]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /--title is required/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("inbox create: missing SHIP_API_TOKEN exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    inboxRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "inbox",
        "create",
        "--type",
        "report",
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
