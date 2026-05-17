import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

/* Smoke-tests for the post-merge ``shipctl run``. The command now does
 * what ``shipctl agent-run`` used to: fetch a pattern, optionally pick
 * a ticket, render the prompt, launch the agent runtime. We exercise
 * only the no-launch paths (``--help``, missing args, ``--dry-run`` for
 * a routine declared in a real .ship/config.yml) so the tests don't
 * need a live SHIP_API_BASE / Cursor key. */

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function runCtl(cwd, args, env = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd,
    env: { ...process.env, ...env },
    encoding: "utf8",
  });
}

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-run-"));
}

function seedShipDir(dir, configBody) {
  fs.mkdirSync(path.join(dir, ".ship"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".ship", "config.yml"), configBody);
}

test("shipctl run --help exits 0 and prints usage", () => {
  const dir = mktmp();
  const r = runCtl(dir, ["run", "--help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl run — execute one E14 routine/);
  assert.match(r.stdout, /--routine <id>/);
});

test("shipctl run rejects missing --routine", () => {
  const dir = mktmp();
  const r = runCtl(dir, ["run"]);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /--routine/);
});

test("shipctl run errors when no .ship/config.yml is found", () => {
  const dir = mktmp();
  const r = runCtl(dir, ["run", "--routine", "anything"]);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /\.ship\/config\.yml not found/);
});

// "unknown routine 'bar'" test removed in ELS-124 cutover: the
// ``process.routines`` map is gone, every ``--routine <slug>`` is
// passed through to ``GET /v1/.../agent-roles/{slug}/resolve``
// which decides whether the slug names a real role. There's no
// local "known routines" set to validate against — the next test
// covers the natural failure mode (API-creds missing).

test("shipctl run requires SHIP_API_BASE+TOKEN+WORKSPACE_ID for agent-role resolve", () => {
  const dir = mktmp();
  seedShipDir(
    dir,
    "version: 2\nprocess:\n  routines:\n    sec:\n      specialist: security-officer\n      trigger:\n        type: schedule\n        cron: '0 6 * * *'\n",
  );
  const r = runCtl(dir, ["run", "--routine", "sec"], {
    // Wipe inherited env so the test isn't sensitive to the developer's
    // local SHIP_API_BASE export.
    SHIP_API_BASE: "",
    SHIP_API_TOKEN: "",
    SHIP_WORKSPACE_ID: "",
  });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /SHIP_API_BASE \+ SHIP_API_TOKEN \+ SHIP_WORKSPACE_ID/);
});

test("shipctl run errors when neither specialist nor pattern is set on the routine", () => {
  const dir = mktmp();
  seedShipDir(
    dir,
    "version: 2\nprocess:\n  routines:\n    standalone:\n      prompt: 'Just an instruction'\n      trigger:\n        type: schedule\n        cron: '0 6 * * *'\n",
  );
  const r = runCtl(dir, ["run", "--routine", "standalone"], {
    SHIP_API_BASE: "https://example.invalid",
    SHIP_API_TOKEN: "fake",
    SHIP_WORKSPACE_ID: "ffffffff-ffff-4fff-8fff-ffffffffffff",
  });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /no 'specialist:'/);
});

