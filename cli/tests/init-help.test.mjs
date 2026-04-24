import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bin = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

function run(script, args) {
  return spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
}

test("shipctl help exits 0 and mentions shipctl", () => {
  const r = run(bin, ["help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /shipctl/);
  assert.match(r.stdout, /Artifacts protocol|artifacts protocol/i);
  assert.doesNotMatch(r.stdout, /^\s*ship\s+(init|search|docs|pattern)\b/m);
});

test("shipctl init help mentions new flags", () => {
  const r = run(bin, ["init", "--help"]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /--agents/);
  assert.match(r.stdout, /--tracker/);
  assert.match(r.stdout, /--ci/);
  assert.match(r.stdout, /--preset/);
  assert.match(r.stdout, /--copy-playbook/);
  assert.match(r.stdout, /shipctl init/);
});

/* P8-01 — the dispatcher had a duplicate `cmd === "doctor"` block, so
 * `shipctl doctor --json` was running the command twice and printing
 * two JSON payloads. Assert the JSON parses as a single payload (i.e.
 * it's not two concatenated objects). */
test("shipctl doctor --json prints exactly one JSON payload", () => {
  const r = run(bin, ["doctor", "--json", "--no-network"]);
  assert.equal(r.status, 0, `${r.stdout}\n${r.stderr}`);
  /* Two payloads concatenated would parse-fail (}{ in the middle).
   * One payload parses cleanly. */
  let parsed;
  try {
    parsed = JSON.parse(r.stdout);
  } catch (err) {
    assert.fail(
      `expected exactly one JSON payload on stdout; got:\n${r.stdout.slice(0, 400)}\n…\nparse error: ${err.message}`,
    );
  }
  assert.ok(parsed && typeof parsed === "object", "expected a JSON object payload");
  /* Belt-and-braces: two concatenated objects would show ``}{`` somewhere
   * (with optional whitespace). One object never produces that pattern. */
  assert.doesNotMatch(
    r.stdout,
    /}\s*{/,
    "stdout appears to contain more than one JSON object — the dispatcher may have run doctor twice",
  );
});

/* P8-04 — sweep stale `ship <verb>` strings out of catalog command
 * help text. All these surfaces print usage when invoked with no
 * args (or with `help`), so we drive each one and assert the prose
 * uses the actual binary name `shipctl ` and never bare `ship `
 * before a verb. */
const SHIP_PREFIX_SCENARIOS = [
  { name: "docs",        args: ["docs"] },
  { name: "search",      args: ["search"] },
  { name: "patterns",    args: ["pattern"] },
  { name: "tools",       args: ["tool"] },
  { name: "collections", args: ["collection"] },
];

for (const { name, args } of SHIP_PREFIX_SCENARIOS) {
  test(`shipctl ${name} usage uses 'shipctl ' (not bare 'ship ') prefix`, () => {
    const r = run(bin, args);
    assert.equal(r.status, 0, `${name} exited ${r.status}\n${r.stderr}`);
    assert.ok(r.stdout.includes("shipctl "), `${name} should print 'shipctl '`);
    /* Ensure no line starts with "ship <verb>" — a bare `ship` prefix
     * (the legacy binary name) would mis-direct adopters who already
     * have shipctl on PATH. We allow the literal token "shipctl"
     * anywhere because `\bship[^c]` excludes "shipctl". */
    const offending = r.stdout
      .split("\n")
      .filter((line) => /^\s*ship\s+\S/.test(line));
    assert.equal(
      offending.length,
      0,
      `${name} usage contains bare 'ship <verb>' line(s):\n${offending.join("\n")}`,
    );
  });
}

