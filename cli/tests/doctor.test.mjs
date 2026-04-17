import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bin = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-doctor-"));
}
function write(dir, rel, contents = "") {
  const full = path.join(dir, rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, contents, "utf8");
}
function runJson(extraArgs = []) {
  const r = spawnSync(process.execPath, [bin, "doctor", "--json", ...extraArgs], {
    encoding: "utf8",
  });
  assert.equal(r.status, 0, r.stderr || r.stdout);
  return JSON.parse(r.stdout);
}

test("empty repo yields adoption-minimum preset, tracker=none, ci=manual", () => {
  const dir = mktmp();
  const report = runJson(["--cwd", dir]);
  assert.equal(report.inferred.preset, "adoption-minimum");
  assert.equal(report.inferred.tracker, "none");
  assert.equal(report.inferred.ci, "manual");
  assert.equal(report.inferred.language, "multi");
  assert.deepEqual(report.inferred.agents, []);
});

test("package.json with react-native infers mobile-app preset", () => {
  const dir = mktmp();
  write(
    dir,
    "package.json",
    JSON.stringify({
      name: "demo",
      dependencies: { "react-native": "0.74.0" },
    }),
  );
  const report = runJson(["--cwd", dir]);
  assert.equal(report.inferred.preset, "mobile-app");
  assert.ok(
    report.preset_evidence.some((e) => /react-native/.test(e)),
    `expected react-native evidence, got ${report.preset_evidence.join("|")}`,
  );
});

test("LINEAR_API_KEY env + gh workflows → tracker=linear (≥0.7), ci=gh-actions (1.0)", () => {
  const dir = mktmp();
  write(dir, ".env.example", "LINEAR_API_KEY=lin_api_placeholder\n");
  write(
    dir,
    ".github/workflows/ci.yml",
    "name: CI\non: [push]\njobs: { t: { runs-on: ubuntu-latest, steps: [{ run: echo hi }] } }\n",
  );

  const report = runJson(["--cwd", dir]);

  assert.equal(report.inferred.tracker, "linear");
  const linear = report.findings.trackers.find((t) => t.id === "linear");
  assert.ok(linear.present, "linear should be present");
  assert.ok(linear.confidence >= 0.7, `linear confidence ${linear.confidence} < 0.7`);

  assert.equal(report.inferred.ci, "gh-actions");
  const gh = report.findings.ci.find((c) => c.id === "gh-actions");
  assert.equal(gh.confidence, 1);
});

test("--write-inventory writes a valid .ship/inventory.json file", () => {
  const dir = mktmp();
  write(dir, "package.json", JSON.stringify({ name: "demo" }));

  const r = spawnSync(
    process.execPath,
    [bin, "doctor", "--cwd", dir, "--write-inventory"],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);

  const invPath = path.join(dir, ".ship", "inventory.json");
  assert.ok(fs.existsSync(invPath), "inventory.json should exist");
  const body = JSON.parse(fs.readFileSync(invPath, "utf8"));

  assert.equal(body.version, 1);
  assert.equal(typeof body.detected_at, "string");
  assert.equal(body.cwd, path.resolve(dir));
  assert.ok(body.findings && Array.isArray(body.findings.trackers));
  assert.ok(Array.isArray(body.findings.ci));
  assert.ok(Array.isArray(body.findings.language));
  assert.ok(Array.isArray(body.findings.agents));
  assert.ok(body.inferred);
  assert.equal(typeof body.inferred.tracker, "string");
  assert.equal(typeof body.inferred.ci, "string");
  assert.equal(typeof body.inferred.preset, "string");
});

test("--json emits parseable JSON with expected top-level keys", () => {
  const dir = mktmp();
  write(dir, "tsconfig.json", "{}");
  const report = runJson(["--cwd", dir]);
  for (const k of [
    "version",
    "detected_at",
    "cwd",
    "findings",
    "inferred",
    "preset_evidence",
    "existing",
    "recommendations",
  ]) {
    assert.ok(Object.prototype.hasOwnProperty.call(report, k), `missing key ${k}`);
  }
  assert.equal(report.inferred.language, "ts");
});

// ── Config-aware reconciliation (Bug D) ───────────────────────────────────

// Minimal `.ship/config.yml` that passes validation. Doctor only reads the
// `stack` / `api.channel` subtrees, so we keep the fixture small.
function writeShipConfig(dir, { tracker = "none", ci = "manual", preset = "adoption-minimum", language = "multi", agents = [], channel = "stable" } = {}) {
  const agentList = agents.length
    ? agents.map((a) => `  - ${a}`).join("\n")
    : "  []";
  const body = `version: 1
shipctl_min: 0.3.0
api:
  base_url: https://ship.elmundi.com
  channel: ${channel}
  ttl_hours: 24
  offline_ok: true
stack:
  tracker: ${tracker}
  ci: ${ci}
  agents:
${agents.length ? agentList : "  []"}
  language: ${language}
  preset: ${preset}
artifacts:
  pins: {}
  auto_update: true
cache:
  vcs_tracked: false
telemetry:
  share: false
  anonymous_id: null
  scope:
    artifact_usage: true
    improvement_drafts: true
    errors: false
`;
  write(dir, ".ship/config.yml", body);
}

test("doctor reconciles config with disk (happy path)", () => {
  const dir = mktmp();
  writeShipConfig(dir, {
    tracker: "linear",
    ci: "gh-actions",
    preset: "mobile-app",
    language: "ts",
    agents: ["cursor", "claude-md", "codex"],
  });
  // Disk signals for all three declared agents (codex lives in AGENTS.md).
  fs.mkdirSync(path.join(dir, ".cursor", "rules"), { recursive: true });
  write(dir, "CLAUDE.md", "# Claude rules\n");
  write(dir, "AGENTS.md", "# Agents rules\n");

  const report = runJson(["--cwd", dir]);

  assert.ok(report.config, "report.config should be present when .ship/config.yml is present");
  assert.equal(report.config.preset, "mobile-app");
  assert.equal(report.config.tracker, "linear");
  assert.equal(report.config.ci, "gh-actions");
  assert.equal(report.config.language, "ts");

  assert.ok(report.reconciled, "report.reconciled should be present");
  assert.equal(report.reconciled.preset, "mobile-app");
  assert.equal(report.reconciled.tracker, "linear");
  assert.equal(report.reconciled.ci, "gh-actions");
  assert.equal(report.reconciled.language, "ts");

  for (const id of ["cursor", "claude-md", "codex"]) {
    assert.ok(
      report.reconciled.agents.includes(id),
      `reconciled.agents should include ${id}: ${report.reconciled.agents.join(",")}`,
    );
  }
  assert.ok(
    !report.reconciled.agents.includes("agents-md"),
    "reconciled.agents must not expose the raw agents-md signal when codex is declared",
  );

  // The top-line recommendation must not contradict config.
  const recs = report.recommendations.join(" | ");
  assert.ok(
    !/--preset\s+adoption-minimum/.test(recs),
    `recommendations must not propose adoption-minimum when config says mobile-app: ${recs}`,
  );
});

test("doctor maps AGENTS.md to codex when config lists codex", () => {
  const dir = mktmp();
  writeShipConfig(dir, { agents: ["codex"] });
  write(dir, "AGENTS.md", "# Agents rules\n");

  const report = runJson(["--cwd", dir]);

  assert.ok(report.reconciled, "report.reconciled should be present");
  assert.ok(
    report.reconciled.agents.includes("codex"),
    `reconciled.agents should include codex: ${report.reconciled.agents.join(",")}`,
  );
  assert.ok(
    !report.reconciled.agents.includes("agents-md"),
    "reconciled.agents should not expose agents-md when config maps it to codex",
  );

  const mapped = (report.reconciled.agent_signals || []).find(
    (s) => s.signal === "agents-md" && s.resolved === "codex",
  );
  assert.ok(mapped, "expected agent_signals to document the agents-md → codex re-map");
  assert.ok(
    mapped.evidence && /AGENTS\.md/i.test(mapped.evidence),
    `agents-md evidence should mention AGENTS.md, got ${mapped.evidence}`,
  );
});
