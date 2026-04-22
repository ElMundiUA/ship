import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-sync-"));
}

function sha256Hex(s) {
  return crypto.createHash("sha256").update(s).digest("hex");
}

const PLURAL_BY_SINGULAR = {
  pattern: "patterns",
  tool: "tools",
  collection: "collections",
};

function startServer({ body, version = "1.0.0", kind = "pattern", id = "role-developer" } = {}) {
  const shaExpected = sha256Hex(body);
  const entry = {
    kind,
    id,
    title: "Developer",
    summary: "test",
    path: `artifacts/${PLURAL_BY_SINGULAR[kind]}/${id}/ARTIFACT.md`,
    tags: [],
    group: "test",
    version,
    content_sha256: shaExpected,
    updated_at: "2026-04-17T09:21:08Z",
    channel: "stable",
    min_shipctl: "0.3.0",
    deprecated: false,
    replaced_by: null,
  };
  const server = http.createServer((req, res) => {
    let chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const url = new URL(req.url, "http://localhost");
      const perKindMatch = url.pathname.match(/^\/(patterns|tools|collections)$/);
      if (req.method === "GET" && perKindMatch) {
        const plural = perKindMatch[1];
        const expectedPlural = PLURAL_BY_SINGULAR[entry.kind];
        const arr = plural === expectedPlural ? [entry] : [];
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ description: "", version: 2, [plural]: arr }));
        return;
      }
      if (req.method === "POST" && url.pathname === "/fetch") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            ...entry,
            content: body,
          }),
        );
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "not_found" }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({
        server,
        baseUrl: `http://127.0.0.1:${addr.port}`,
      });
    });
  });
}

/** Async spawn that runs the parent event loop so in-process HTTP servers can accept connections. */
function runCtlAsync(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      cwd: opts.cwd,
      env: { ...process.env, ...(opts.env || {}) },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => {
      stdout += d.toString("utf8");
    });
    child.stderr.on("data", (d) => {
      stderr += d.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      resolve({ status: code, signal, stdout, stderr });
    });
  });
}

test("sync --check-only reports updated:1; second sync reports up_to_date:1", async () => {
  const body = "# Cloud Developer\n\nbody bytes\n";
  const { server, baseUrl } = await startServer({ body });
  try {
    const dir = mktmp();
    spawnSync("git", ["init", "-q"], { cwd: dir });

    const init = await runCtlAsync(["config", "init", "--cwd", dir]);
    assert.equal(init.status, 0, init.stderr);

    const setPin = await runCtlAsync([
      "config",
      "set",
      "artifacts.pins.pattern/role-developer",
      "1.0.0",
      "--cwd",
      dir,
    ]);
    assert.equal(setPin.status, 0, setPin.stderr);

    const check = await runCtlAsync([
      "--base-url",
      baseUrl,
      "sync",
      "--check-only",
      "--cwd",
      dir,
    ]);
    assert.equal(check.status, 0, check.stderr);
    assert.match(check.stdout, /updated:\s*1/);
    assert.match(check.stdout, /up_to_date:\s*0/);

    const first = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(first.status, 0, first.stderr);
    assert.match(first.stdout, /updated:\s*1/);

    const bodyPath = path.join(
      dir,
      ".ship",
      "cache",
      "pattern",
      "role-developer@1.0.0",
      "ARTIFACT.md",
    );
    assert.ok(fs.existsSync(bodyPath), `expected cache at ${bodyPath}`);
    assert.equal(fs.readFileSync(bodyPath, "utf8"), body);

    const second = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(second.status, 0, second.stderr);
    assert.match(second.stdout, /up_to_date:\s*1/);
    assert.match(second.stdout, /updated:\s*0/);

    const state = JSON.parse(
      fs.readFileSync(path.join(dir, ".ship", "state.json"), "utf8"),
    );
    assert.ok(state.last_sync_at);
    assert.ok(state.last_manifest_hash);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("sync re-fetches when the cached .md body drifted from its meta sha (Bug F)", async () => {
  const body = "# Cloud Developer\n\nbody bytes\n";
  const { server, baseUrl } = await startServer({ body });
  try {
    const dir = mktmp();
    spawnSync("git", ["init", "-q"], { cwd: dir });
    await runCtlAsync(["config", "init", "--cwd", dir]);
    await runCtlAsync([
      "config",
      "set",
      "artifacts.pins.pattern/role-developer",
      "1.0.0",
      "--cwd",
      dir,
    ]);

    // Seed the cache via a normal sync.
    const first = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(first.status, 0, first.stderr);
    assert.match(first.stdout, /updated:\s*1/);

    const bodyPath = path.join(
      dir,
      ".ship",
      "cache",
      "pattern",
      "role-developer@1.0.0",
      "ARTIFACT.md",
    );
    // Corrupt the on-disk body without updating the sidecar meta.
    fs.appendFileSync(bodyPath, "\nEXTRA\n");

    const repair = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(repair.status, 0, repair.stderr);
    assert.match(repair.stdout, /updated:\s*1/);
    assert.match(repair.stdout, /up_to_date:\s*0/);
    assert.match(
      repair.stdout,
      /refetch:.*pattern\/role-developer@1\.0\.0.*drifted/,
      `expected a 'refetch: …drifted' note, got: ${repair.stdout}`,
    );
    assert.equal(fs.readFileSync(bodyPath, "utf8"), body);

    // Third run should be up_to_date again.
    const third = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(third.status, 0, third.stderr);
    assert.match(third.stdout, /up_to_date:\s*1/);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("sync re-fetches when the cached .md body was deleted while meta remains (Bug F)", async () => {
  const body = "# Cloud Developer\n\nbody bytes\n";
  const { server, baseUrl } = await startServer({ body });
  try {
    const dir = mktmp();
    spawnSync("git", ["init", "-q"], { cwd: dir });
    await runCtlAsync(["config", "init", "--cwd", dir]);
    await runCtlAsync([
      "config",
      "set",
      "artifacts.pins.pattern/role-developer",
      "1.0.0",
      "--cwd",
      dir,
    ]);

    const first = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(first.status, 0, first.stderr);

    const bodyPath = path.join(
      dir,
      ".ship",
      "cache",
      "pattern",
      "role-developer@1.0.0",
      "ARTIFACT.md",
    );
    const metaPath = path.join(
      dir,
      ".ship",
      "cache",
      "pattern",
      "role-developer@1.0.0",
      ".meta.json",
    );
    fs.rmSync(bodyPath);
    assert.ok(fs.existsSync(metaPath), "meta sidecar should remain");

    const repair = await runCtlAsync(["--base-url", baseUrl, "sync", "--cwd", dir]);
    assert.equal(repair.status, 0, repair.stderr);
    assert.match(repair.stdout, /updated:\s*1/);
    assert.match(repair.stdout, /refetch:.*missing/);
    assert.ok(fs.existsSync(bodyPath), "sync should rewrite the body");
    assert.equal(fs.readFileSync(bodyPath, "utf8"), body);
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("sync --dry-run prints planned HTTP calls without writing cache", async () => {
  const body = "# Body\n";
  const { server, baseUrl } = await startServer({ body });
  try {
    const dir = mktmp();
    spawnSync("git", ["init", "-q"], { cwd: dir });
    await runCtlAsync(["config", "init", "--cwd", dir]);
    await runCtlAsync([
      "config",
      "set",
      "artifacts.pins.pattern/role-developer",
      "1.0.0",
      "--cwd",
      dir,
    ]);

    const dry = await runCtlAsync([
      "--base-url",
      baseUrl,
      "sync",
      "--dry-run",
      "--cwd",
      dir,
    ]);
    assert.equal(dry.status, 0, dry.stderr);
    assert.match(dry.stdout, /plan: GET .*\/\{patterns,tools,collections\}/);
    assert.match(dry.stdout, /plan: POST .*\/fetch/);

    const bodyPath = path.join(
      dir,
      ".ship",
      "cache",
      "pattern",
      "role-developer@1.0.0",
      "ARTIFACT.md",
    );
    assert.equal(fs.existsSync(bodyPath), false);
  } finally {
    await new Promise((r) => server.close(r));
  }
});
