import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { detectAgentTargets } from "../lib/detect.mjs";

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-detect-"));
}
function write(dir, rel, contents = "") {
  const full = path.join(dir, rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, contents, "utf8");
}
function mkdir(dir, rel) {
  fs.mkdirSync(path.join(dir, rel), { recursive: true });
}

test("empty repo yields no targets", () => {
  const dir = mktmp();
  assert.deepEqual(detectAgentTargets(dir), []);
});

test("definitive markers produce confidence 1", () => {
  const dir = mktmp();
  write(dir, "AGENTS.md");
  write(dir, "CLAUDE.md");
  write(dir, ".clinerules");
  write(dir, ".windsurfrules");
  write(dir, "GEMINI.md");
  write(dir, ".aider.conf.yml");
  write(dir, ".github/copilot-instructions.md");

  const targets = detectAgentTargets(dir);
  const byId = Object.fromEntries(targets.map((t) => [t.id, t]));

  assert.equal(byId["agents-md"].confidence, 1);
  assert.equal(byId["claude-md"].confidence, 1);
  assert.equal(byId.cline.confidence, 1);
  assert.equal(byId.windsurf.confidence, 1);
  assert.equal(byId.gemini.confidence, 1);
  assert.equal(byId.aider.confidence, 1);
  assert.equal(byId.copilot.confidence, 1);
});

test("dir-only markers yield confidence 0.5", () => {
  const dir = mktmp();
  mkdir(dir, ".continue");
  mkdir(dir, ".zed");
  mkdir(dir, ".opencode");
  mkdir(dir, ".gemini");

  const byId = Object.fromEntries(detectAgentTargets(dir).map((t) => [t.id, t]));
  assert.equal(byId.continue.confidence, 0.5);
  assert.equal(byId.zed.confidence, 0.5);
  assert.equal(byId.opencode.confidence, 0.5);
  assert.equal(byId.gemini.confidence, 0.5);
});

test("cursor + cursor-cloud coexist when environments.json present", () => {
  const dir = mktmp();
  mkdir(dir, ".cursor/rules");
  write(dir, ".cursor/environments.json", "{}");

  const ids = detectAgentTargets(dir).map((t) => t.id);
  assert.ok(ids.includes("cursor"));
  assert.ok(ids.includes("cursor-cloud"));
});

test("results are sorted by confidence descending", () => {
  const dir = mktmp();
  mkdir(dir, ".continue");
  write(dir, "AGENTS.md");

  const targets = detectAgentTargets(dir);
  for (let i = 1; i < targets.length; i++) {
    assert.ok(targets[i - 1].confidence >= targets[i].confidence);
  }
});

test("each target returns {id, label, paths, confidence}", () => {
  const dir = mktmp();
  write(dir, "AGENTS.md");
  const [t] = detectAgentTargets(dir);
  assert.equal(typeof t.id, "string");
  assert.equal(typeof t.label, "string");
  assert.ok(Array.isArray(t.paths) && t.paths.length > 0);
  assert.equal(typeof t.confidence, "number");
});
