import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import YAML from "yaml";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-init-"));
}

function sha256Hex(s) {
  return crypto.createHash("sha256").update(s).digest("hex");
}

function agentRulesCursorBody() {
  return `---
artifact_kind: collection
subkind: agent-rules
agent_id: cursor
install_target: ".cursor/rules/ship-artifacts-protocol.mdc"
marker: "<!-- ship-cli: artifacts-protocol v1 -->"
---

<!-- ship-cli: artifacts-protocol v1 -->

# Ship artifacts — Cursor

Resolve via \`shipctl\`; record \`kind:id@version\` in PRs.

<!-- ship-cli:end artifacts-protocol -->
`;
}

function presetMobileAppBody() {
  return `---
artifact_kind: collection
subkind: preset
preset_id: mobile-app
compatible_ci: [gh-actions]
compatible_trackers: [linear]
---

# Preset — Mobile application

Labels: platform:ios, platform:android, store:review, flag:behind,
flag:ahead, change-record, blocked, preview:ready.
`;
}

function startServer(manifest) {
  const bodies = {};
  const entries = manifest.map((e) => {
    const body = e.body;
    bodies[`${e.kind}:${e.id}:${e.version}`] = body;
    return {
      kind: e.kind,
      id: e.id,
      title: e.title || e.id,
      summary: "test",
      path: `x/${e.id}.md`,
      tags: [],
      group: "test",
      version: e.version,
      content_sha256: sha256Hex(body),
      updated_at: "2026-04-17T09:21:08Z",
      channel: "stable",
      min_shipctl: "0.3.0",
      deprecated: false,
      replaced_by: null,
      yanked: false,
    };
  });
  const PLURAL_BY_SINGULAR = {
    pattern: "patterns",
    tool: "tools",
    collection: "collections",
  };
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const url = new URL(req.url, "http://localhost");
      const perKindMatch = url.pathname.match(/^\/(patterns|tools|collections)$/);
      if (req.method === "GET" && perKindMatch) {
        const plural = perKindMatch[1];
        const arr = entries.filter((e) => PLURAL_BY_SINGULAR[e.kind] === plural);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ description: "", version: 2, [plural]: arr }));
        return;
      }
      if (req.method === "POST" && url.pathname === "/fetch") {
        const raw = Buffer.concat(chunks).toString("utf8");
        const body = raw ? JSON.parse(raw) : {};
        const entry = entries.find(
          (e) =>
            e.kind === body.kind &&
            e.id === body.id &&
            (!body.version || e.version === body.version),
        );
        if (!entry) {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "version_not_found" }));
          return;
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            ...entry,
            content: bodies[`${entry.kind}:${entry.id}:${entry.version}`],
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
      resolve({ server, baseUrl: `http://127.0.0.1:${addr.port}` });
    });
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
    child.stdout.on("data", (d) => { stdout += d.toString("utf8"); });
    child.stderr.on("data", (d) => { stderr += d.toString("utf8"); });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      resolve({ status: code, signal, stdout, stderr });
    });
  });
}

function mkrepo() {
  const dir = mktmp();
  spawnSync("git", ["init", "-q"], { cwd: dir });
  return dir;
}

test("init writes config, gitignore, state without copy-rules", async () => {
  // Phase 2.5 retired ``--copy-rules``; the wizard's seed PR now
  // installs the agent rule files. ``shipctl init`` is back to its
  // original surface — write ``.ship/config.yml`` + ``.gitignore`` +
  // ``.ship/state.json`` and exit.
  const dir = mkrepo();
  const r = await runCtlAsync([
    "init",
    "--yes",
    "--agents", "cursor",
    "--telemetry", "off",
    "--cwd", dir,
  ]);
  assert.equal(r.status, 0, r.stderr);

  const cfgPath = path.join(dir, ".ship", "config.yml");
  assert.ok(fs.existsSync(cfgPath), "config.yml should exist");
  const cfg = YAML.parse(fs.readFileSync(cfgPath, "utf8"));
  assert.equal(cfg.telemetry.share, false);
  assert.deepEqual(cfg.stack.agents, ["cursor"]);
  assert.equal(cfg.version, 2);

  const gi = fs.readFileSync(path.join(dir, ".gitignore"), "utf8");
  assert.match(gi, /^\.ship\/cache\/$/m);

  const statePath = path.join(dir, ".ship", "state.json");
  assert.ok(fs.existsSync(statePath), "state.json should exist");
});

test("init --dry-run writes no files", async () => {
  const { server, baseUrl } = await startServer([
    {
      kind: "collection",
      id: "agent-rules-cursor",
      version: "1.0.0",
      body: agentRulesCursorBody(),
    },
  ]);
  try {
    const dir = mkrepo();
    const r = await runCtlAsync([
      "--base-url", baseUrl,
      "init",
      "--yes",
      "--dry-run",
      "--agents", "cursor",
      "--copy-rules",
      "--bootstrap",
      "--preset", "mobile-app",
      "--tracker", "linear",
      "--ci", "gh-actions",
      "--telemetry", "off",
      "--cwd", dir,
    ]);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(fs.existsSync(path.join(dir, ".ship", "config.yml")), false);
    assert.equal(fs.existsSync(path.join(dir, ".cursor", "rules", "ship-artifacts-protocol.mdc")), false);
    assert.equal(fs.existsSync(path.join(dir, "SHIP_BOOTSTRAP_PLAN.md")), false);
    assert.equal(fs.existsSync(path.join(dir, ".github", "workflows", "ship-pilot.yml")), false);
    assert.equal(fs.existsSync(path.join(dir, ".ship", "labels.yml")), false);
    assert.equal(fs.existsSync(path.join(dir, ".env.example")), false);
    assert.match(r.stdout, /dry-run: no files written/);
  } finally {
    await new Promise((res) => server.close(res));
  }
});

test("init --bootstrap mobile-app + gh-actions + linear writes skeletons", async () => {
  const { server, baseUrl } = await startServer([
    {
      kind: "collection",
      id: "agent-rules-cursor",
      version: "1.0.0",
      body: agentRulesCursorBody(),
    },
    {
      kind: "collection",
      id: "preset-mobile-app",
      version: "1.0.0",
      body: presetMobileAppBody(),
    },
  ]);
  try {
    const dir = mkrepo();
    const r = await runCtlAsync([
      "--base-url", baseUrl,
      "init",
      "--yes",
      "--agents", "cursor",
      "--tracker", "linear",
      "--ci", "gh-actions",
      "--preset", "mobile-app",
      "--copy-rules",
      "--bootstrap",
      "--telemetry", "off",
      "--cwd", dir,
    ]);
    assert.equal(r.status, 0, r.stderr);

    const planPath = path.join(dir, "SHIP_BOOTSTRAP_PLAN.md");
    assert.ok(fs.existsSync(planPath));
    const plan = fs.readFileSync(planPath, "utf8");
    assert.match(plan, /preset.*mobile-app/);

    const wf = path.join(dir, ".github", "workflows", "ship-pilot.yml");
    assert.ok(fs.existsSync(wf));
    const wfText = fs.readFileSync(wf, "utf8");
    assert.match(wfText, /name: ship-pilot/);
    assert.match(wfText, /ship-managed: workflow/);
    assert.match(wfText, /build-ios:/);
    assert.match(wfText, /build-android:/);

    const labels = path.join(dir, ".ship", "labels.yml");
    assert.ok(fs.existsSync(labels));
    const labelsText = fs.readFileSync(labels, "utf8");
    for (const l of [
      "platform:ios",
      "platform:android",
      "store:review",
      "flag:behind",
      "flag:ahead",
      "change-record",
      "blocked",
      "preview:ready",
    ]) {
      assert.match(labelsText, new RegExp(l.replace(/:/g, ":")), `${l} missing`);
    }

    const env = path.join(dir, ".env.example");
    assert.ok(fs.existsSync(env));
    const envText = fs.readFileSync(env, "utf8");
    assert.match(envText, /ship-managed/);
    for (const k of ["LINEAR_API_KEY=", "LINEAR_TEAM_ID=", "GITHUB_TOKEN=", "EXPO_TOKEN=", "SENTRY_AUTH_TOKEN="]) {
      assert.match(envText, new RegExp(`^${k}`, "m"));
    }
  } finally {
    await new Promise((res) => server.close(res));
  }
});

test("init --telemetry off writes telemetry.share=false", async () => {
  const { server, baseUrl } = await startServer([
    {
      kind: "collection",
      id: "agent-rules-cursor",
      version: "1.0.0",
      body: agentRulesCursorBody(),
    },
  ]);
  try {
    const dir = mkrepo();
    const r = await runCtlAsync([
      "--base-url", baseUrl,
      "init",
      "--yes",
      "--agents", "cursor",
      "--telemetry", "off",
      "--cwd", dir,
    ]);
    assert.equal(r.status, 0, r.stderr);
    const cfg = YAML.parse(fs.readFileSync(path.join(dir, ".ship", "config.yml"), "utf8"));
    assert.equal(cfg.telemetry.share, false);
  } finally {
    await new Promise((res) => server.close(res));
  }
});

test("init --json emits structured summary", async () => {
  // Phase 2.5 — no rule install path here; the JSON summary still
  // carries the empty ``rules: []`` slot for back-compat with
  // existing CI consumers.
  const dir = mkrepo();
  const r = await runCtlAsync([
    "--json",
    "init",
    "--yes",
    "--agents", "cursor",
    "--telemetry", "off",
    "--cwd", dir,
  ]);
  assert.equal(r.status, 0, r.stderr);
  const parsed = JSON.parse(r.stdout);
  assert.equal(parsed.telemetry, "off");
  assert.deepEqual(parsed.stack.agents, ["cursor"]);
});
