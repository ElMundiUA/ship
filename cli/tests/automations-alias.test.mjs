import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

/* `shipctl automations` is the operator-friendly soft alias for
 * `shipctl lanes` (Phase 8). Both commands ship indefinitely; this
 * test guards the dispatch so the two surfaces never silently
 * diverge. We only assert outputs match for the read-only `list`
 * subcommand — `install` / `remove` mutate disk and are already
 * covered by lanes.test.mjs. */

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-automations-"));
}

function runCtl(cwd, args) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd,
    env: { ...process.env },
    encoding: "utf8",
  });
}

test("shipctl automations --help exits 0 and mentions the lane surface", () => {
  const r = runCtl(process.cwd(), ["automations", "--help"]);
  assert.equal(r.status, 0, `stderr: ${r.stderr}`);
  assert.ok(r.stdout.length > 0, "automations --help produced empty stdout");
  /* Help should reference either the literal `lanes` surface or the
   * console-facing "Automation" noun (the lanes printHelp does both). */
  assert.match(
    r.stdout,
    /lanes|Automation/,
    "automations --help should mention 'lanes' or 'Automation'",
  );
});

test("shipctl automations list --json matches shipctl lanes list --json", () => {
  /* Use a tmp dir without a .ship/ tree so both commands hit the
   * same loadConfig() error path. The lanes handler prints to stderr
   * and exits 2; the alias must do exactly the same. */
  const dir = mktmp();
  const lanes = runCtl(dir, ["lanes", "list", "--json"]);
  const auto = runCtl(dir, ["automations", "list", "--json"]);
  assert.equal(
    auto.status,
    lanes.status,
    `exit codes diverge: lanes=${lanes.status} automations=${auto.status}`,
  );
  assert.equal(auto.stdout, lanes.stdout, "stdout differs between alias and original");
  /* stderr may include absolute tmp paths; only compare the trailing
   * segment after the cwd to avoid false positives if the test ever
   * runs in a sandbox that resolves /var → /private/var (macOS). */
  function tail(s) {
    return s.replace(dir, "<TMP>");
  }
  assert.equal(tail(auto.stderr), tail(lanes.stderr), "stderr differs between alias and original");
});

test("shipctl automations list --json — when JSON is emitted, both parse to the same shape", () => {
  /* Seed a minimal v2 config with one lane so `lanes list --json`
   * actually produces a payload; both dispatch paths must yield the
   * same parsed object. */
  const dir = mktmp();
  const cfgPath = path.join(dir, ".ship", "config.yml");
  fs.mkdirSync(path.dirname(cfgPath), { recursive: true });
  fs.writeFileSync(
    cfgPath,
    [
      "version: 2",
      "shipctl_min: 0.12.0",
      "preset: monorepo",
      "repo: org/thing",
      "api:",
      "  base_url: https://ship.example.com",
      "  channel: stable",
      "stack:",
      "  tracker: linear",
      "  ci: gh-actions",
      "  agents: [cursor]",
      "  language: multi",
      "  preset: monorepo",
      "agent:",
      "  default:",
      "    provider: cursor-cloud",
      "  overrides: {}",
      "lanes:",
      "  pr_review:",
      "    kind: event",
      "    pattern: flow-pr-self-review",
      "    on: pull_request",
      "artifacts:",
      "  pins: {}",
      "  auto_update: true",
      "cache:",
      "  vcs_tracked: false",
      "telemetry:",
      "  share: false",
      "  anonymous_id: null",
      "  scope:",
      "    artifact_usage: true",
      "",
    ].join("\n"),
    "utf8",
  );

  const lanes = runCtl(dir, ["lanes", "list", "--json"]);
  const auto = runCtl(dir, ["automations", "list", "--json"]);
  assert.equal(lanes.status, 0, `lanes failed: ${lanes.stderr}`);
  assert.equal(auto.status, 0, `automations failed: ${auto.stderr}`);
  assert.deepEqual(
    JSON.parse(auto.stdout),
    JSON.parse(lanes.stdout),
    "alias should emit identical JSON payload",
  );
});
