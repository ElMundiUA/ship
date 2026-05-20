import { test } from "node:test";
import assert from "node:assert/strict";
import { closeMockServer, startMockServer } from "./helpers/mock-workspace-api.mjs";
import { runShipctl } from "./helpers/run-shipctl.mjs";

function projectRouter(req, res, { body }, created = true) {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (req.method === "POST" && url.pathname.endsWith("/projects/find-or-create")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        created,
        project: { id: "proj-1", name: body?.name ?? "?" },
        received: body,
      }),
    );
    return;
  }
  res.writeHead(404);
  res.end();
}

test("project find-or-create: created true TSV output", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    projectRouter(req, res, ctx, true),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "project",
      "find-or-create",
      "--name",
      "Tech Debt",
      "--body",
      "Project body",
    ]);
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /proj-1\tTech Debt\tcreated/);
    const post = requests.find((q) => q.method === "POST");
    assert.equal(post.body.name, "Tech Debt");
    assert.equal(post.body.body, "Project body");
  } finally {
    await closeMockServer(server);
  }
});

test("project find-or-create: created false existing", async () => {
  const { server, baseUrl } = await startMockServer((req, res, ctx) =>
    projectRouter(req, res, ctx, false),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "project",
      "find-or-create",
      "--name",
      "Tech Debt",
      "--body",
      "ignored on find",
    ]);
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /existing/);
  } finally {
    await closeMockServer(server);
  }
});

test("project find-or-create: --json matches server payload", async () => {
  const { server, baseUrl } = await startMockServer((req, res, ctx) =>
    projectRouter(req, res, ctx, true),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "project",
      "find-or-create",
      "--name",
      "QA Debt",
      "--body",
      "Body",
      "--json",
    ]);
    assert.equal(r.status, 0, r.stderr);
    const out = JSON.parse(r.stdout);
    assert.equal(out.created, true);
    assert.equal(out.project.id, "proj-1");
    assert.equal(out.project.name, "QA Debt");
  } finally {
    await closeMockServer(server);
  }
});

test("project find-or-create: missing --name exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    projectRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl([
      "--base-url",
      baseUrl,
      "project",
      "find-or-create",
      "--body",
      "only body",
    ]);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /--name is required/);
    assert.equal(requests.length, 0);
  } finally {
    await closeMockServer(server);
  }
});

test("project find-or-create: missing SHIP_API_TOKEN exits 1", async () => {
  const { server, baseUrl, requests } = await startMockServer((req, res, ctx) =>
    projectRouter(req, res, ctx),
  );
  try {
    const r = await runShipctl(
      [
        "--base-url",
        baseUrl,
        "project",
        "find-or-create",
        "--name",
        "Tech Debt",
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
