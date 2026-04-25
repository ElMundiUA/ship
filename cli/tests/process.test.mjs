import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import YAML from "yaml";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-process-"));
}

function writeConfig(dir, config) {
  const file = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, YAML.stringify(config), "utf8");
}

function baseConfig() {
  return {
    version: 2,
    shipctl_min: "0.12.0",
    api: { base_url: "https://ship.example.com", channel: "stable" },
    stack: { tracker: "linear", ci: "gh-actions", preset: "adoption-minimum", language: "multi" },
    agent: { default: { provider: null }, overrides: {} },
    process: {
      id: "development",
      name: "Development Process",
      primary: true,
      states: [
        {
          id: "ba_requirements",
          name: "Requirements",
          specialist: {
            id: "business_analyst",
            name: "Business analyst",
            agent_profile: "cursor_agent",
          },
          instructions: "Turn the ticket into acceptance criteria before handoff.",
        },
        {
          id: "dev_implementation",
          name: "Implementation",
          specialist: { id: "developer", name: "Developer" },
        },
      ],
      transitions: [
        {
          from: "ba_requirements",
          to: "dev_implementation",
          condition: "requirements approved",
        },
      ],
      routines: [],
    },
    lanes: {},
    artifacts: { pins: {}, auto_update: true },
    cache: { vcs_tracked: false },
    telemetry: {
      share: false,
      anonymous_id: null,
      scope: { artifact_usage: true, improvement_drafts: true, errors: false },
    },
  };
}

function runCtl(args, env = {}) {
  const childEnv = { ...process.env, ...env };
  delete childEnv.NODE_OPTIONS;
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    encoding: "utf8",
    env: childEnv,
    timeout: 10_000,
  });
}

function runCtlAsync(args, env = {}) {
  const childEnv = { ...process.env, ...env };
  delete childEnv.NODE_OPTIONS;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
    }, 10_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (status, signal) => {
      clearTimeout(timer);
      resolve({ status, signal, stdout, stderr });
    });
  });
}

async function startTicketServer() {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({
      method: req.method,
      url: req.url,
      authorization: req.headers.authorization,
    });
    if (req.method === "GET" && req.url?.startsWith("/v1/workspaces/ws-1/processes/development/tickets")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          tracker: "linear",
          tickets: [
            {
              id: "LIN-42",
              title: "Clarify checkout flow",
              url: "https://linear.app/acme/issue/LIN-42",
              status: "Todo",
            },
          ],
        }),
      );
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}`,
    requests,
    close: () => {
      server.closeAllConnections();
      return new Promise((resolve) => server.close(resolve));
    },
  };
}

test("process prompt emits specialist bundle JSON", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  const policies = path.join(dir, "policies.md");
  fs.writeFileSync(policies, "# Workspace policies\n\nNo secrets in commits.\n", "utf8");

  const result = runCtl([
    "process",
    "prompt",
    "--state",
    "ba_requirements",
    "--ticket-json",
    JSON.stringify({ key: "SHIP-42", title: "Clarify checkout flow" }),
    "--policies-file",
    policies,
    "--cwd",
    dir,
    "--json",
  ]);

  assert.equal(result.status, 0, result.stderr);
  const body = JSON.parse(result.stdout);
  assert.equal(body.process.id, "development");
  assert.equal(body.state.id, "ba_requirements");
  assert.equal(body.specialist.id, "business_analyst");
  assert.equal(body.agent_profile, "cursor_agent");
  assert.equal(body.ticket.key, "SHIP-42");
  assert.match(body.policies, /No secrets/);
  assert.deepEqual(body.allowed_transitions, [
    {
      from: "ba_requirements",
      to: "dev_implementation",
      condition: "requirements approved",
    },
  ]);
  assert.match(body.guardrails, /Before inventing/);
});

test("process prompt markdown includes ticket, policies, and allowed transitions", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  const result = runCtl([
    "process",
    "prompt",
    "--specialist",
    "business_analyst",
    "--ticket-title",
    "Checkout bug",
    "--cwd",
    dir,
  ]);

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /# Ship Specialist Prompt Bundle/);
  assert.match(result.stdout, /Checkout bug/);
  assert.match(result.stdout, /`ba_requirements` -> `dev_implementation`/);
  assert.match(result.stdout, /Workspace policies were not supplied locally/);
  assert.match(result.stdout, /Do not perform direct ticket-system mutations/);
});

test("process prompt rejects missing selector", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  const result = runCtl(["process", "prompt", "--cwd", dir]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /--state/);
});

test("process prompt is runner-compatible stdout only and has no run side effects", () => {
  const dir = mktmp();
  writeConfig(dir, baseConfig());
  const result = runCtl(
    [
      "process",
      "prompt",
      "--state",
      "ba_requirements",
      "--ticket-key",
      "SHIP-77",
      "--cwd",
      dir,
    ],
    {
      SHIP_CALLBACK_URL: "https://ship.example.test/v1/pipelines/runs/abc/result",
      SHIP_RUN_TOKEN: "runner-token",
      SHIP_RUN_ID: "00000000-0000-4000-8000-000000000000",
    },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  assert.match(result.stdout, /# Ship Specialist Prompt Bundle/);
  assert.match(result.stdout, /SHIP-77/);
  assert.equal(
    fs.existsSync(path.join(dir, ".ship", "state")),
    false,
    "process prompt must not write idempotency markers or callback state",
  );
});

test("process tickets calls read-only picker endpoint", async () => {
  const server = await startTicketServer();
  try {
    const result = await runCtlAsync(
      [
        "process",
        "tickets",
        "--workspace",
        "ws-1",
        "--query",
        "checkout",
        "--tracker",
        "linear",
        "--limit",
        "5",
        "--base-url",
        server.url,
        "--json",
      ],
      { SHIP_API_TOKEN: "test-token" },
    );

    assert.equal(result.status, 0, result.stderr);
    const body = JSON.parse(result.stdout);
    assert.equal(body.tracker, "linear");
    assert.equal(body.tickets[0].id, "LIN-42");
    assert.equal(server.requests.length, 1);
    assert.equal(server.requests[0].method, "GET");
    assert.match(server.requests[0].url, /query=checkout/);
    assert.match(server.requests[0].url, /tracker=linear/);
    assert.equal(server.requests[0].authorization, "Bearer test-token");
  } finally {
    await server.close();
  }
});
