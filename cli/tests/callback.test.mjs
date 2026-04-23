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
  buildOutcome,
  parseArtifactArg,
  parseEscalationArg,
  parseSeverityArg,
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

/* ------------------------------------------------------------------ */
/* RFC-0010 Phase 3 — RunSummary outcome contract                      */
/* ------------------------------------------------------------------ */

const FIXTURES_DIR = path.resolve(__dirname, "fixtures");

/* Convenience: e2e env that disables the env-outcome path so a leaking
 * SHIP_RUN_OUTCOME / SHIP_RUN_OUTCOME_FILE in the developer shell
 * doesn't poison "no outcome flags" assertions. We pass empty strings
 * (not undefined) because spawn merges parent process.env when keys
 * are missing — only an empty value reliably overrides. */
const NO_ENV_OUTCOME = {
  SHIP_RUN_OUTCOME: "",
  SHIP_RUN_OUTCOME_FILE: "",
};

test("parseArtifactArg: TYPE:TITLE:REF, escaped colons, missing REF", () => {
  assert.deepEqual(parseArtifactArg("pr:Fix null check:https://x/1"), {
    type: "pr",
    title: "Fix null check",
    ref: "https://x/1",
  });
  /* Escaped colon inside TITLE is preserved verbatim; trailing URL
   * still parsed as REF because its colons are post-second-separator. */
  assert.deepEqual(
    parseArtifactArg("pr:Fix\\: that bug:https://x/1"),
    { type: "pr", title: "Fix: that bug", ref: "https://x/1" },
  );
  /* No REF — `ref` key omitted, not set to null/empty string. */
  const out = parseArtifactArg("issue:Stale runbook");
  assert.equal(out.type, "issue");
  assert.equal(out.title, "Stale runbook");
  assert.equal("ref" in out, false);
});

test("parseEscalationArg: enum gating + reason taken verbatim", () => {
  assert.deepEqual(
    parseEscalationArg("approval:autofix_proposed"),
    { type: "approval", reason: "autofix_proposed" },
  );
  /* REASON may carry colons / URLs / spaces. */
  assert.deepEqual(
    parseEscalationArg("failure:play_failed_repeatedly: 3 in a row"),
    { type: "failure", reason: "play_failed_repeatedly: 3 in a row" },
  );
});

test("parseSeverityArg: validates vocabulary + integer values", () => {
  assert.deepEqual(parseSeverityArg("HIGH=2"), { key: "high", value: 2 });
  assert.deepEqual(parseSeverityArg("low=0"), { key: "low", value: 0 });
});

test("buildOutcome: omits when no env + no flags (backwards compat)", () => {
  const a = parseCallbackArgs(["--status", "ok"]);
  assert.equal(buildOutcome(a, {}), null);
});

test("buildOutcome: derives findings_count from severity sum when absent", () => {
  const a = parseCallbackArgs([
    "--status",
    "ok",
    "--severity",
    "low=2",
    "--severity",
    "high=3",
  ]);
  const o = buildOutcome(a, {});
  assert.equal(o.findings_count, 5);
  assert.deepEqual(o.findings_by_severity, { low: 2, high: 3 });
});

test("callback_with_outcome_text_includes_outcome_object", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--outcome-text",
        "3 issues found · 1 PR opened",
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const got = mock.received[0];
    assert.equal(got.body.status, "succeeded");
    assert.ok(got.body.outcome, "outcome key present");
    assert.equal(got.body.outcome.outcome_text, "3 issues found · 1 PR opened");
  } finally {
    await mock.close();
  }
});

test("callback_with_severity_aggregates_findings_by_severity", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--severity",
        "low=1",
        "--severity",
        "medium=2",
        "--severity",
        "high=1",
        /* Same severity twice — last write wins per RFC. */
        "--severity",
        "high=4",
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const o = mock.received[0].body.outcome;
    assert.deepEqual(o.findings_by_severity, { low: 1, medium: 2, high: 4 });
    /* Auto-derived rollup: 1 + 2 + 4 = 7 */
    assert.equal(o.findings_count, 7);
  } finally {
    await mock.close();
  }
});

test("callback_artifact_flag_parses_type_title_ref", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--artifact",
        "pr:Fix null check:https://github.com/o/r/pull/42",
        /* Escaped colon inside title; URL ref still parsed correctly. */
        "--artifact",
        "issue:Refactor\\: tidy auth:https://github.com/o/r/issues/7",
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const arts = mock.received[0].body.outcome.artifacts;
    assert.equal(arts.length, 2);
    assert.deepEqual(arts[0], {
      type: "pr",
      title: "Fix null check",
      ref: "https://github.com/o/r/pull/42",
    });
    assert.deepEqual(arts[1], {
      type: "issue",
      title: "Refactor: tidy auth",
      ref: "https://github.com/o/r/issues/7",
    });
  } finally {
    await mock.close();
  }
});

test("callback_artifact_flag_without_ref_omits_field", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--artifact",
        "doc:Architecture decision record",
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const art = mock.received[0].body.outcome.artifacts[0];
    assert.deepEqual(art, {
      type: "doc",
      title: "Architecture decision record",
    });
    assert.equal("ref" in art, false);
  } finally {
    await mock.close();
  }
});

test("callback_requires_approval_flag_sets_top_level_boolean", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      ["callback", "--status", "ok", "--requires-approval"],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    assert.equal(mock.received[0].body.outcome.requires_approval, true);
  } finally {
    await mock.close();
  }
});

test("callback_approval_payload_inline_json_parses", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--requires-approval",
        "--approval-payload",
        '{"kind":"merge_pr","pr_number":42,"risk":"low"}',
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    assert.deepEqual(mock.received[0].body.outcome.approval_payload, {
      kind: "merge_pr",
      pr_number: 42,
      risk: "low",
    });
  } finally {
    await mock.close();
  }
});

test("callback_approval_payload_at_file_loads_from_disk", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--requires-approval",
        "--approval-payload",
        `@${path.join(FIXTURES_DIR, "approval-payload.json")}`,
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const payload = mock.received[0].body.outcome.approval_payload;
    assert.equal(payload.kind, "merge_pr");
    assert.equal(payload.pr_number, 42);
    assert.equal(payload.diff_summary.files_changed, 3);
  } finally {
    await mock.close();
  }
});

test("callback_approval_payload_invalid_json_exits_11", () => {
  const r = runCtlSync(
    [
      "callback",
      "--status",
      "ok",
      "--approval-payload",
      "{not valid json",
      "--callback-url",
      "http://localhost:1/x",
    ],
    {
      ...NO_ENV_OUTCOME,
      SHIP_RUN_TOKEN: "tkn",
    },
  );
  assert.equal(r.status, 11);
  assert.match(r.stderr, /--approval-payload/);
  assert.match(r.stderr, /not valid JSON|valid JSON/i);
});

test("callback_escalation_flag_parses_type_reason", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "fail",
        "--escalation",
        "failure:play_failed_repeatedly",
        "--escalation",
        "improvement:recurring_finding_detected",
      ],
      {
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    assert.deepEqual(mock.received[0].body.outcome.escalations, [
      { type: "failure", reason: "play_failed_repeatedly" },
      { type: "improvement", reason: "recurring_finding_detected" },
    ]);
  } finally {
    await mock.close();
  }
});

test("callback_escalation_invalid_type_exits_2", () => {
  const r = runCtlSync(
    [
      "callback",
      "--status",
      "ok",
      "--escalation",
      "aproval:typo_in_type",
      "--callback-url",
      "http://localhost:1/x",
    ],
    {
      ...NO_ENV_OUTCOME,
      SHIP_RUN_TOKEN: "tkn",
    },
  );
  assert.equal(r.status, 2);
  assert.match(r.stderr, /--escalation TYPE must be one of/);
});

test("callback_env_outcome_file_parses_and_merges", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        /* CLI overlays a single severity — env's other fields survive. */
        "--severity",
        "critical=1",
      ],
      {
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
        SHIP_RUN_OUTCOME_FILE: path.join(FIXTURES_DIR, "run-outcome.json"),
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const o = mock.received[0].body.outcome;
    /* Env's outcome_text passes through (CLI didn't override it). */
    assert.equal(o.outcome_text, "env-provided baseline · 2 issues");
    /* findings_by_severity merges per-key — env's low+high persist
     * alongside CLI's critical. */
    assert.deepEqual(o.findings_by_severity, {
      low: 1,
      high: 1,
      critical: 1,
    });
    /* Env's artifacts pass through — CLI didn't replace them. */
    assert.equal(o.artifacts.length, 1);
    assert.equal(o.artifacts[0].type, "issue");
    /* Env's escalations preserved. */
    assert.equal(o.escalations.length, 1);
    assert.equal(o.escalations[0].type, "improvement");
  } finally {
    await mock.close();
  }
});

test("callback_env_outcome_invalid_json_exits_11", () => {
  const r = runCtlSync(
    [
      "callback",
      "--status",
      "ok",
      "--callback-url",
      "http://localhost:1/x",
    ],
    {
      ...NO_ENV_OUTCOME,
      SHIP_RUN_TOKEN: "tkn",
      SHIP_RUN_OUTCOME: "{this isn't json",
    },
  );
  assert.equal(r.status, 11);
  assert.match(r.stderr, /SHIP_RUN_OUTCOME/);
});

test("callback_no_outcome_flags_omits_outcome_key", async () => {
  /* Backwards compatibility: a call that uses only the pre-P3 flags
   * must produce a request body byte-identical to the legacy contract.
   * No `outcome` key, no surprise fields — adopters on older Ship
   * backends should be able to upgrade `shipctl` without coordination. */
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
        ...NO_ENV_OUTCOME,
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const body = mock.received[0].body;
    assert.deepEqual(body, {
      status: "succeeded",
      summary: "Processed 1 ticket",
      metrics: { tickets: 1 },
    });
    assert.equal("outcome" in body, false);
  } finally {
    await mock.close();
  }
});

test("callback_cli_overrides_env_on_collision", async () => {
  const mock = await startMockShip();
  try {
    const r = await runCtl(
      [
        "callback",
        "--status",
        "ok",
        "--outcome-text",
        "CLI sentence wins",
      ],
      {
        SHIP_RUN_TOKEN: "tkn",
        SHIP_CALLBACK_URL: `${mock.url}/v1/pipelines/runs/r-1/result`,
        SHIP_RUN_OUTCOME: JSON.stringify({
          outcome_text: "env sentence loses",
          findings_count: 9,
        }),
      },
    );
    assert.equal(r.status, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
    const o = mock.received[0].body.outcome;
    assert.equal(o.outcome_text, "CLI sentence wins");
    /* Env-only fields the CLI didn't touch survive the merge. */
    assert.equal(o.findings_count, 9);
  } finally {
    await mock.close();
  }
});
