import { test } from "node:test";
import assert from "node:assert/strict";

import { runAgent, SUPPORTED_PROVIDERS, DEFAULT_PROVIDER } from "../lib/agents/index.mjs";
import { runShipAgent } from "../lib/agents/ship.mjs";

/* Thesis-6 self-spawn adapter (ELS-241). */

test("ship is wired but cursor stays the default", () => {
  assert.ok(SUPPORTED_PROVIDERS.includes("ship"));
  assert.equal(DEFAULT_PROVIDER, "cursor");
  for (const p of ["cursor", "codex", "claude"]) {
    assert.ok(SUPPORTED_PROVIDERS.includes(p));
  }
});

test("runAgent('ship') is dogfood-gated", async () => {
  delete process.env.SHIP_ALLOW_SELF_SPAWN;
  await assert.rejects(
    () => runAgent("ship", { branchName: "b", prompt: "p" }),
    /dogfood-gated/,
  );
});

test("runShipAgent validates inputs before spawning", async () => {
  await assert.rejects(() => runShipAgent({ prompt: "p" }), /branchName required/);
  await assert.rejects(
    () => runShipAgent({ branchName: "b" }),
    /prompt required/,
  );
  delete process.env.SHIP_API_TOKEN;
  await assert.rejects(
    () => runShipAgent({ branchName: "b", prompt: "p" }),
    /SHIP_API_TOKEN/,
  );
});

test("runShipAgent spawns `shipctl run` with the brief in env", async () => {
  // Intercept spawn via a PATH shim: create a fake `shipctl` that
  // records argv + env and exits 0.
  const { mkdtempSync, writeFileSync, readFileSync, chmodSync } = await import("node:fs");
  const { join } = await import("node:path");
  const os = await import("node:os");
  const dir = mkdtempSync(join(os.tmpdir(), "ship-spawn-"));
  const record = join(dir, "record.json");
  const shim = join(dir, "shipctl");
  writeFileSync(
    shim,
    `#!/bin/sh\nprintf '{"argv":"%s","brief":"%s"}' "$*" "$SHIP_SELF_SPAWN_BRIEF" > ${record}\nexit 0\n`,
  );
  chmodSync(shim, 0o755);

  const prevPath = process.env.PATH;
  process.env.PATH = `${dir}:${prevPath}`;
  process.env.SHIP_API_TOKEN = "tok";
  try {
    const res = await runShipAgent({
      workdir: dir,
      branchName: "cursor/ship-test-1",
      prompt: "nested brief",
      onLog: () => {},
    });
    assert.equal(res.status, "FINISHED");
    assert.equal(res.exitCode, 0);
    assert.equal(res.agentId, "ship-cursor/ship-test-1");
    const rec = JSON.parse(readFileSync(record, "utf8"));
    assert.equal(rec.argv, "run");
    assert.equal(rec.brief, "nested brief");
  } finally {
    process.env.PATH = prevPath;
    delete process.env.SHIP_API_TOKEN;
  }
});
