import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawn, spawnSync } from "node:child_process";

import { createDraft, listDrafts, readDraft, draftsDir } from "../lib/feedback/drafts.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-fb-"));
}

function runCtl(args, { cwd, env } = {}) {
  return spawnSync(process.execPath, [SHIPCTL_BIN, ...args], {
    cwd: cwd || process.cwd(),
    env: { ...process.env, ...(env || {}) },
    encoding: "utf8",
  });
}

function runCtlAsync(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      cwd: opts.cwd,
      env: { ...process.env, ...(opts.env || {}) },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    child.on("error", reject);
    child.on("close", (code) => resolve({ status: code, stdout, stderr }));
  });
}

function initRepo(dir) {
  spawnSync("git", ["init", "-q"], { cwd: dir });
  const r = runCtl(["config", "init", "--cwd", dir]);
  assert.equal(r.status, 0, r.stderr);
  return dir;
}

test("createDraft writes front-matter + body; listDrafts enumerates", () => {
  const dir = mktmp();
  initRepo(dir);
  const fp = createDraft(dir, {
    kind: "pattern",
    id: "cloud-developer",
    version: "1.4.2",
    title: "Missing mobile preview step",
    summary: "Evidence checklist misses mobile preview",
    recommendation: "Add a bullet under Evidence",
    stack: { tracker: "linear", ci: "gh-actions", agents: ["cursor"], preset: "web-app" },
  });

  assert.ok(fs.existsSync(fp));
  assert.match(fp, /feedback-drafts/);

  const text = fs.readFileSync(fp, "utf8");
  assert.match(text, /^---\n/);
  assert.match(text, /kind: pattern/);
  assert.match(text, /id: cloud-developer/);
  assert.match(text, /# Missing mobile preview step/);
  assert.match(text, /\*\*Summary\*\*: Evidence checklist/);
  assert.match(text, /\*\*Recommendation\*\*: Add a bullet/);
  assert.match(text, /tracker=linear, ci=gh-actions, agents=cursor, preset=web-app/);
  assert.match(text, /<!-- ship-feedback: v1 -->/);

  const { meta, body } = readDraft(fp);
  assert.equal(meta.kind, "pattern");
  assert.equal(meta.id, "cloud-developer");
  assert.equal(meta.version, "1.4.2");
  assert.equal(meta.title, "Missing mobile preview step");
  assert.ok(body.includes("Missing mobile preview step"));

  const list = listDrafts(dir);
  assert.equal(list.length, 1);
  assert.equal(list[0], fp);
});

test("feedback draft command creates a file with front-matter", () => {
  const dir = mktmp();
  initRepo(dir);
  const r = runCtl([
    "feedback",
    "draft",
    "--kind",
    "pattern",
    "--id",
    "cloud-developer",
    "--version",
    "1.0.0",
    "--title",
    "example",
    "--summary",
    "demo feedback",
    "--cwd",
    dir,
  ]);
  assert.equal(r.status, 0, r.stderr);
  const fp = r.stdout.trim().split(/\n/).pop();
  assert.ok(fs.existsSync(fp));
  const text = fs.readFileSync(fp, "utf8");
  assert.match(text, /kind: pattern/);
  assert.match(text, /id: cloud-developer/);
  assert.match(text, /# example/);

  const list = runCtl(["feedback", "list", "--cwd", dir]);
  assert.equal(list.status, 0, list.stderr);
  assert.match(list.stdout, /pattern\/cloud-developer@1\.0\.0 — example/);
});

test("feedback submit: missing title fails with exit 1", () => {
  const dir = mktmp();
  initRepo(dir);
  // Write a draft directly with no title in front-matter.
  const drafts = draftsDir(dir);
  fs.mkdirSync(drafts, { recursive: true });
  const fp = path.join(drafts, "2026-04-17-10-00-00-pattern-x.md");
  fs.writeFileSync(
    fp,
    `---\nkind: pattern\nid: x\nversion: "1.0.0"\ntags: []\ncreated_at: 2026-04-17T10:00:00Z\n---\n\n# (untitled)\n\n**Summary**: lacks title\n`,
    "utf8",
  );

  const r = runCtl(["feedback", "submit", fp, "--yes", "--cwd", dir]);
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /missing required fields/);
});

function startFeedbackServer({ status = 200, response } = {}) {
  let received = null;
  const resp = response || {
    issue_url: "https://example.com/issues/42",
    issue_number: 42,
    labels: ["feedback"],
    deduplicated: false,
  };
  const server = http.createServer((req, res) => {
    let chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        received = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        received = null;
      }
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(resp));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, baseUrl: `http://127.0.0.1:${addr.port}`, getReceived: () => received });
    });
  });
}

test("feedback submit: draft moved to sent/ on success", async () => {
  const dir = mktmp();
  initRepo(dir);
  const r = runCtl([
    "feedback",
    "draft",
    "--kind",
    "pattern",
    "--id",
    "cloud-developer",
    "--version",
    "1.0.0",
    "--title",
    "Missing mobile preview step",
    "--summary",
    "Evidence checklist misses mobile preview",
    "--recommendation",
    "Add a bullet",
    "--cwd",
    dir,
  ]);
  assert.equal(r.status, 0, r.stderr);
  const draftFp = r.stdout.trim().split(/\n/).pop();
  assert.ok(fs.existsSync(draftFp));

  const { server, baseUrl, getReceived } = await startFeedbackServer();
  try {
    const sub = await runCtlAsync(
      [
        "--base-url",
        baseUrl,
        "feedback",
        "submit",
        draftFp,
        "--yes",
        "--cwd",
        dir,
      ],
    );
    assert.equal(sub.status, 0, sub.stderr);
    assert.match(sub.stdout, /example\.com\/issues\/42/);
    assert.match(sub.stdout, /moved:.*sent/);

    assert.equal(fs.existsSync(draftFp), false);
    const sentDir = path.join(draftsDir(dir), "sent");
    const files = fs.readdirSync(sentDir);
    assert.equal(files.length, 1);

    const body = getReceived();
    assert.equal(body.title, "Missing mobile preview step");
    assert.equal(body.artifact.kind, "pattern");
    assert.equal(body.artifact.id, "cloud-developer");
    assert.equal(body.artifact.version, "1.0.0");
    assert.deepEqual(body.recommendations, ["Add a bullet"]);
  } finally {
    await new Promise((r) => server.close(r));
  }
});
