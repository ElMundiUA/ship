import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

/* Phase 4: ``shipctl preflight`` is the one-shot env + role contract
 * checker the trigger workflow runs before launching the runner.
 * Exit 0 always so the workflow can branch on the JSON ``ready``
 * field; the missing-secret list pinpoints what the operator owes
 * the runner. */
function runPreflight(args, env = {}) {
  return spawnSync(
    process.execPath,
    [BIN, "preflight", ...args],
    {
      encoding: "utf8",
      // Strip Ship + Cursor secrets from the inherited env so the
      // tests are deterministic across operator workstations.
      env: {
        ...process.env,
        SHIP_API_TOKEN: "",
        SHIP_API_BASE: "",
        SHIP_WORKSPACE_API_BASE: "",
        SHIP_WORKSPACE_ID: "",
        CURSOR_API_KEY: "",
        ...env,
      },
    },
  );
}

test("preflight: missing secrets surface as a structured ready=false", () => {
  const r = runPreflight(["--json", "--cwd", "/tmp"]);
  assert.equal(r.status, 0, `exit ${r.status}\n${r.stderr}`);
  const body = JSON.parse(r.stdout);
  assert.equal(body.ready, false);
  assert.ok(Array.isArray(body.missing_secrets));
  for (const required of ["SHIP_API_TOKEN", "SHIP_WORKSPACE_ID", "SHIP_API_BASE"]) {
    assert.ok(
      body.missing_secrets.includes(required),
      `missing_secrets should include ${required} when env is empty; got ${body.missing_secrets.join(", ")}`,
    );
  }
  // Cursor key only flagged when provider resolves to cursor (the
  // workspace default), and there's no .ship/config.yml in /tmp so
  // we still hit the cursor default path.
  assert.ok(body.missing_secrets.includes("CURSOR_API_KEY"));
});

test("preflight: every required secret present → ready=true", () => {
  const r = runPreflight(["--json", "--cwd", "/tmp"], {
    SHIP_API_TOKEN: "tok",
    SHIP_API_BASE: "https://api.example",
    SHIP_WORKSPACE_ID: "00000000-0000-0000-0000-000000000001",
    CURSOR_API_KEY: "ck",
  });
  assert.equal(r.status, 0, `exit ${r.status}\n${r.stderr}`);
  const body = JSON.parse(r.stdout);
  assert.equal(body.ready, true);
  assert.deepEqual(body.missing_secrets, []);
});
