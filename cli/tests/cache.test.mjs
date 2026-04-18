import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import {
  writeCached,
  readCached,
  listCached,
  removeCached,
  verifyCached,
  verifyCachedOnDisk,
  readCachedFrontMatter,
  readCachedArtifact,
  cachePath,
  cacheFolder,
  metaPath,
  migrateLegacyCache,
} from "../lib/cache/store.mjs";

const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "bin",
  "shipctl.mjs",
);

function sha256Hex(s) {
  return crypto.createHash("sha256").update(s).digest("hex");
}

function startFetchServer({ kind, id, version, body }) {
  const shaExpected = sha256Hex(body);
  const entry = {
    kind,
    id,
    title: `${id}`,
    summary: "test",
    path: `collections/${id}.md`,
    tags: [],
    group: "test",
    version,
    content_sha256: shaExpected,
    updated_at: "2026-04-18T09:00:00Z",
    channel: "stable",
    min_shipctl: "0.3.0",
    deprecated: false,
    replaced_by: null,
  };
  const PLURAL_BY_SINGULAR = {
    pattern: "patterns",
    workflow: "workflows",
    tool: "tools",
    collection: "collections",
  };
  const server = http.createServer((req, res) => {
    let chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const url = new URL(req.url, "http://localhost");
      const perKindMatch = url.pathname.match(/^\/(patterns|workflows|tools|collections)$/);
      if (req.method === "GET" && perKindMatch) {
        const plural = perKindMatch[1];
        const expectedPlural = PLURAL_BY_SINGULAR[entry.kind];
        const arr = plural === expectedPlural ? [entry] : [];
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ description: "", version: 2, [plural]: arr }));
        return;
      }
      if (req.method === "POST" && url.pathname === "/fetch") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ...entry, content: body }));
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "not_found" }));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, baseUrl: `http://127.0.0.1:${addr.port}` });
    });
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
    child.on("close", (code, signal) => resolve({ status: code, signal, stdout, stderr }));
  });
}

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-cache-"));
}

test("writeCached -> readCached round-trips", () => {
  const root = mktmp();
  const content = "# Hello\nWorld\n";
  const { meta } = writeCached(root, "pattern", "cloud-developer", "1.4.2", content, {
    updated_at: "2026-04-17T09:21:08Z",
    source_url: "https://ship.example/api/fetch",
  });
  assert.ok(meta.content_sha256);
  const got = readCached(root, "pattern", "cloud-developer", "1.4.2");
  assert.ok(got);
  assert.equal(got.content, content);
  assert.equal(got.meta.content_sha256, meta.content_sha256);
});

test("sanitize replaces slashes in id for cache folder name", () => {
  const root = mktmp();
  const p = cachePath(root, "collection", "agent-rules/cursor", "1.0.0");
  assert.ok(
    p.endsWith(path.join("agent-rules__cursor@1.0.0", "ARTIFACT.md")),
    `unexpected cache path: ${p}`,
  );
  writeCached(root, "collection", "agent-rules/cursor", "1.0.0", "body", {});
  const back = readCached(root, "collection", "agent-rules/cursor", "1.0.0");
  assert.equal(back.content, "body");
});

test("listCached enumerates cached entries", () => {
  const root = mktmp();
  writeCached(root, "pattern", "a", "1.0.0", "x", {});
  writeCached(root, "tool", "b", "2.0.0", "y", {});
  const items = listCached(root);
  const kinds = items.map((x) => `${x.kind}/${x.id}@${x.version}`).sort();
  assert.deepEqual(kinds, ["pattern/a@1.0.0", "tool/b@2.0.0"]);
});

test("verifyCached detects tampering", () => {
  const root = mktmp();
  writeCached(root, "pattern", "x", "1.0.0", "original", {});
  const ok = verifyCached(root, "pattern", "x", "1.0.0");
  assert.equal(ok.ok, true);

  const body = cachePath(root, "pattern", "x", "1.0.0");
  fs.writeFileSync(body, "tampered");
  const bad = verifyCached(root, "pattern", "x", "1.0.0");
  assert.equal(bad.ok, false);
  assert.notEqual(bad.expected, bad.actual);
});

test("removeCached removes the artifact folder (body + meta)", () => {
  const root = mktmp();
  writeCached(root, "pattern", "x", "1.0.0", "x", {});
  const folder = cacheFolder(root, "pattern", "x", "1.0.0");
  assert.ok(fs.existsSync(folder));
  const n = removeCached(root, "pattern", "x", "1.0.0");
  assert.equal(n, 2);
  assert.equal(fs.existsSync(folder), false);
  assert.equal(readCached(root, "pattern", "x", "1.0.0"), null);
});

test("verifyCachedOnDisk: ok for intact entry, missing_body when body deleted, drift when body mutated", () => {
  const root = mktmp();
  writeCached(root, "collection", "preset-web-app", "1.0.0", "hello\n", {});

  const ok = verifyCachedOnDisk(root, "collection", "preset-web-app", "1.0.0");
  assert.equal(ok.ok, true);
  assert.ok(ok.actual_sha);

  const body = cachePath(root, "collection", "preset-web-app", "1.0.0");
  fs.rmSync(body);
  const missing = verifyCachedOnDisk(root, "collection", "preset-web-app", "1.0.0");
  assert.equal(missing.ok, false);
  assert.equal(missing.reason, "missing_body");

  writeCached(root, "collection", "preset-web-app", "1.0.0", "hello\n", {});
  fs.writeFileSync(cachePath(root, "collection", "preset-web-app", "1.0.0"), "tampered\n");
  const drift = verifyCachedOnDisk(root, "collection", "preset-web-app", "1.0.0");
  assert.equal(drift.ok, false);
  assert.equal(drift.reason, "drift");
  assert.notEqual(drift.actual_sha, drift.expected_sha);
});

test("readCachedFrontMatter parses install_target and returns body/meta", () => {
  const root = mktmp();
  const md = [
    "---",
    "artifact_kind: collection",
    "subkind: agent-rules",
    "agent_id: codex",
    'install_target: "AGENTS.md"',
    "---",
    "",
    "# Ship artifacts — Codex",
    "body here",
    "",
  ].join("\n");
  writeCached(root, "collection", "agent-rules-codex", "1.0.0", md, {
    source_url: "about:test",
  });
  const fm = readCachedFrontMatter(root, "collection", "agent-rules-codex", "1.0.0");
  assert.ok(fm);
  assert.equal(fm.fm.install_target, "AGENTS.md");
  assert.equal(fm.fm.agent_id, "codex");
  assert.match(fm.body, /# Ship artifacts/);
  assert.equal(fm.version, "1.0.0");
  assert.ok(fm.meta.content_sha256);
});

test("readCachedFrontMatter picks highest-version cached entry when version omitted", () => {
  const root = mktmp();
  const mk = (v) => `---\ninstall_target: "CLAUDE.md"\nversion: "${v}"\n---\n\nbody ${v}\n`;
  writeCached(root, "collection", "agent-rules-claude-md", "1.0.0", mk("1.0.0"), {});
  writeCached(root, "collection", "agent-rules-claude-md", "1.2.0", mk("1.2.0"), {});
  const fm = readCachedFrontMatter(root, "collection", "agent-rules-claude-md");
  assert.ok(fm);
  assert.equal(fm.version, "1.2.0");
  assert.equal(fm.fm.install_target, "CLAUDE.md");
});

test("readCachedFrontMatter returns null when nothing is cached", () => {
  const root = mktmp();
  assert.equal(readCachedFrontMatter(root, "collection", "agent-rules-missing"), null);
});

test("readCachedArtifact surfaces v2 spec.install_target", () => {
  const root = mktmp();
  const md = [
    "---",
    "artifact_kind: collection",
    "id: agent-rules-codex",
    "name: Codex agent rules",
    "version: 1.2.3",
    "tags: [agent, codex]",
    "spec:",
    "  install_target: AGENTS.md",
    "  marker: \"<!-- ship-cli: artifacts-protocol v1 -->\"",
    "---",
    "",
    "# Codex rules body",
    "",
  ].join("\n");
  writeCached(root, "collection", "agent-rules-codex", "1.2.3", md, {});
  const art = readCachedArtifact(root, "collection", "agent-rules-codex", "1.2.3");
  assert.ok(art);
  assert.equal(art.spec.install_target, "AGENTS.md");
  assert.equal(art.fm.spec && art.fm.spec.install_target, "AGENTS.md");
  assert.deepEqual(art.fm.tags, ["agent", "codex"]);
  assert.match(art.body, /# Codex rules body/);
});

test("migrateLegacyCache moves <id>@<v>.md + .meta.json into the new folder layout", () => {
  const root = mktmp();
  const kind = "collection";
  const id = "agent-rules-cursor";
  const version = "1.0.0";
  const dir = path.join(root, ".ship", "cache", kind);
  fs.mkdirSync(dir, { recursive: true });
  const legacyBody = path.join(dir, `${id}@${version}.md`);
  const legacyMeta = path.join(dir, `${id}@${version}.meta.json`);
  const md = "---\ninstall_target: \".cursor/rules/ship.mdc\"\n---\n\nbody\n";
  fs.writeFileSync(legacyBody, md, "utf8");
  fs.writeFileSync(
    legacyMeta,
    JSON.stringify({ kind, id, version, content_sha256: sha256Hex(md) }),
    "utf8",
  );

  migrateLegacyCache(root);

  assert.equal(fs.existsSync(legacyBody), false);
  assert.equal(fs.existsSync(legacyMeta), false);
  const newBody = path.join(cacheFolder(root, kind, id, version), "ARTIFACT.md");
  const newMeta = metaPath(root, kind, id, version);
  assert.ok(fs.existsSync(newBody));
  assert.ok(fs.existsSync(newMeta));

  const cached = readCached(root, kind, id, version);
  assert.equal(cached.content, md);
  assert.equal(cached.meta.id, id);

  // Migration is implicitly invoked by readCached/listCached, so calling it
  // again should be a no-op (idempotent).
  migrateLegacyCache(root);
  assert.ok(fs.existsSync(newBody));
});

test("readCached lazily migrates a legacy single-file entry", () => {
  const root = mktmp();
  const kind = "pattern";
  const id = "cloud-developer";
  const version = "1.4.2";
  const dir = path.join(root, ".ship", "cache", kind);
  fs.mkdirSync(dir, { recursive: true });
  const body = "# legacy body\n";
  const meta = { kind, id, version, content_sha256: sha256Hex(body) };
  fs.writeFileSync(path.join(dir, `${id}@${version}.md`), body, "utf8");
  fs.writeFileSync(
    path.join(dir, `${id}@${version}.meta.json`),
    JSON.stringify(meta),
    "utf8",
  );

  const got = readCached(root, kind, id, version);
  assert.ok(got);
  assert.equal(got.content, body);
  // After read, the legacy single-file path should no longer exist.
  assert.equal(fs.existsSync(path.join(dir, `${id}@${version}.md`)), false);
  assert.ok(fs.existsSync(path.join(cacheFolder(root, kind, id, version), "ARTIFACT.md")));
});

test("shipctl collection fetch <id> writes to .ship/cache when in a workspace (Bug C)", async () => {
  const body = "---\nartifact_kind: collection\n---\n\n# Pharma addendum\nhello\n";
  const kind = "collection";
  const id = "addendum-pharma";
  const version = "1.0.0";
  const { server, baseUrl } = await startFetchServer({ kind, id, version, body });
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-fetch-cache-"));
    spawnSync("git", ["init", "-q"], { cwd: dir });
    const init = await runCtlAsync(["config", "init", "--cwd", dir]);
    assert.equal(init.status, 0, init.stderr);

    const res = await runCtlAsync(
      ["--base-url", baseUrl, "collection", "fetch", id],
      { cwd: dir },
    );
    assert.equal(res.status, 0, res.stderr);
    assert.match(
      res.stdout,
      /cached: collection\/addendum-pharma@1\.0\.0/,
      `stdout was: ${res.stdout}`,
    );
    // By default the body should NOT be dumped to stdout (opt-in via --print).
    assert.doesNotMatch(res.stdout, /# Pharma addendum/);

    const bodyPath = path.join(dir, ".ship", "cache", kind, `${id}@${version}`, "ARTIFACT.md");
    assert.ok(fs.existsSync(bodyPath), `expected cache body at ${bodyPath}`);
    assert.equal(fs.readFileSync(bodyPath, "utf8"), body);

    const metaJsonPath = path.join(dir, ".ship", "cache", kind, `${id}@${version}`, ".meta.json");
    const meta = JSON.parse(fs.readFileSync(metaJsonPath, "utf8"));
    assert.equal(meta.content_sha256, sha256Hex(body));
    assert.equal(meta.kind, kind);
    assert.equal(meta.id, id);
    assert.equal(meta.version, version);
    assert.equal(meta.channel, "stable");
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("shipctl collection fetch <id> --print also echoes the body to stdout", async () => {
  const body = "---\nartifact_kind: collection\n---\n\n# Body on stdout\n";
  const { server, baseUrl } = await startFetchServer({
    kind: "collection",
    id: "example",
    version: "1.0.0",
    body,
  });
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-fetch-print-"));
    spawnSync("git", ["init", "-q"], { cwd: dir });
    await runCtlAsync(["config", "init", "--cwd", dir]);
    const res = await runCtlAsync(
      ["--base-url", baseUrl, "collection", "fetch", "example", "--print"],
      { cwd: dir },
    );
    assert.equal(res.status, 0, res.stderr);
    assert.match(res.stdout, /cached: collection\/example@1\.0\.0/);
    assert.match(res.stdout, /# Body on stdout/);
  } finally {
    await new Promise((r) => server.close(r));
  }
});
