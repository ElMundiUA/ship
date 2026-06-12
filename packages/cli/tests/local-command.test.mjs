/**
 * ELS-248 acceptance criteria as executable asserts.
 *
 * The local executor must live entirely outside the (b) control
 * plane: no tracker/next picker, no dispatch lease, no push, no PR,
 * no finish endpoint. We pin that with a source-level scan — the
 * cheapest faithful encoding of the ticket's "verified by grep over
 * the new command" criterion — plus arg-parsing sanity via the
 * --help path.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  path.join(here, "..", "lib", "commands", "local.mjs"),
  "utf8",
);

test("local.mjs never references the ticket picker or finish endpoint", () => {
  assert.ok(!source.includes("tracker/next"), "must not call /tracker/next");
  assert.ok(!source.includes("agent-runs/finish"), "must not call /agent-runs/finish");
  assert.ok(!source.includes("getNextTask"), "must not import the picker helper");
});

test("local.mjs never imports push/PR helpers", () => {
  assert.ok(!source.includes("pushBranch"), "must not push");
  assert.ok(!source.includes("openPullRequest"), "must not open PRs");
  assert.ok(!source.includes("commitAndPr"), "no commit-and-pr mode");
  assert.ok(!/git\(\["push/.test(source), "no raw git push either");
});

test("local.mjs never touches the dispatcher / lease surface", () => {
  assert.ok(!/dispatch/i.test(source.replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\//g, "").replace(/\*[^\n]*/g, "")), "no dispatcher calls outside comments");
  assert.ok(!source.includes("/agent-dispatch"), "no lease endpoints");
});

test("local.mjs uses the shared runAgent adapter and the local prompt mode", () => {
  assert.ok(source.includes("runAgent(provider"), "reuses runAgent");
  assert.ok(source.includes('mode: "local"'), "renders the ELS-246 local prompt");
  assert.ok(source.includes("worktree"), "scratch worktree based");
});

test("escalation goes through tracker/tickets create only", () => {
  assert.ok(source.includes("/tracker/tickets"), "escalation = create_issue");
});
