import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bin = path.resolve(__dirname, "..", "bin", "shipctl.mjs");

// Point network calls at an unreachable host so `shipctl init` can't hang on
// real artifact fetches. init.mjs wraps syncArtifacts() in try/catch so the
// command still writes .ship/config.yml even when sync fails.
const OFFLINE_ENV = { ...process.env, SHIP_API_BASE: "http://127.0.0.1:1" };

function mktmp() {
  // Resolve symlinks (macOS /var → /private/var) so subprocess cwd and the
  // test's expectations agree when we compare absolute paths.
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "shipctl-new-")));
}

function runNew(args, { cwd } = {}) {
  return spawnSync(process.execPath, [bin, "new", ...args], {
    cwd: cwd || process.cwd(),
    env: OFFLINE_ENV,
    encoding: "utf8",
  });
}

test("new --dry-run --json plans a target without touching disk", () => {
  const parent = mktmp();
  const targetName = "demo-dry";
  const r = runNew(
    [targetName, "--preset", "adoption-minimum", "--yes", "--dry-run", "--json"],
    { cwd: parent },
  );
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  assert.equal(parsed.dry_run, true);
  assert.equal(parsed.created_dir, true);
  assert.equal(
    path.resolve(parsed.dir),
    path.resolve(parent, targetName),
    "dir in summary should resolve to <parent>/<name>",
  );
  assert.ok(
    !fs.existsSync(path.join(parent, targetName)),
    "dry-run must not create the directory",
  );
  assert.ok(Array.isArray(parsed.planned_files));
  assert.ok(parsed.planned_files.some((f) => f.endsWith("README.md")));
  assert.ok(parsed.planned_files.some((f) => f.endsWith("config.yml")));
});

test("new <name> --yes creates dir, git init, README, .ship/config.yml", () => {
  const parent = mktmp();
  const targetName = "demo-live";
  const r = runNew(
    [targetName, "--preset", "adoption-minimum", "--yes", "--agents", "cursor"],
    { cwd: parent },
  );
  // init may print sync warning because of unreachable API; still must exit 0.
  assert.equal(r.status, 0, `stderr:\n${r.stderr}\nstdout:\n${r.stdout}`);
  const dir = path.join(parent, targetName);
  assert.ok(fs.existsSync(path.join(dir, ".git")), ".git should exist");
  assert.ok(fs.existsSync(path.join(dir, "README.md")), "README.md should exist");
  assert.ok(
    fs.existsSync(path.join(dir, ".ship", "config.yml")),
    ".ship/config.yml should exist",
  );
  const readme = fs.readFileSync(path.join(dir, "README.md"), "utf8");
  assert.match(readme, new RegExp(`# ${targetName}`));
  assert.match(readme, /shipctl/);
});

test("new --here reuses an existing git dir", () => {
  const dir = mktmp();
  const git = spawnSync("git", ["init", "-q"], { cwd: dir, encoding: "utf8" });
  assert.equal(git.status, 0, git.stderr);

  const r = runNew(
    ["--here", "--preset", "adoption-minimum", "--yes"],
    { cwd: dir },
  );
  assert.equal(r.status, 0, `stderr:\n${r.stderr}\nstdout:\n${r.stdout}`);
  assert.ok(
    fs.existsSync(path.join(dir, ".ship", "config.yml")),
    "should have seeded .ship/config.yml in the existing dir",
  );
  // git dir should still exist (not nuked)
  assert.ok(fs.existsSync(path.join(dir, ".git")));
});

test("new --help documents --preset/--agents/--here", () => {
  const r = spawnSync(process.execPath, [bin, "new", "--help"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /--preset/);
  assert.match(r.stdout, /--agents/);
  assert.match(r.stdout, /--here/);
});

test("shipctl new forwards all flags to init and defaults bootstrap/copy-rules", () => {
  const parent = mktmp();
  const targetName = "demo-forward";
  const r = runNew(
    [
      targetName,
      "--preset",
      "mobile-app",
      "--tracker",
      "linear",
      "--ci",
      "gh-actions",
      "--agents",
      "cursor,codex",
      "--language",
      "ts",
      "--channel",
      "stable",
      "--base-url",
      "http://127.0.0.1:8100",
      "--telemetry",
      "off",
      "--dry-run",
      "--json",
    ],
    { cwd: parent },
  );
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  assert.equal(parsed.dry_run, true);
  assert.equal(parsed.run_init, true);
  const argv = parsed.init_argv;
  assert.ok(Array.isArray(argv) && argv[0] === "init", `init_argv[0] should be "init": ${JSON.stringify(argv)}`);

  const has = (flag) => argv.includes(flag);
  const valueFor = (flag) => {
    const i = argv.indexOf(flag);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
  };

  assert.ok(has("--yes"), "init argv must include --yes");
  assert.ok(has("--copy-rules"), "init argv must include --copy-rules by default");
  assert.ok(has("--bootstrap"), "init argv must include --bootstrap by default");
  assert.ok(has("--json"), "init argv must forward --json when caller asked for JSON");

  assert.equal(valueFor("--preset"), "mobile-app");
  assert.equal(valueFor("--tracker"), "linear");
  assert.equal(valueFor("--ci"), "gh-actions");
  assert.equal(valueFor("--agents"), "cursor,codex");
  assert.equal(valueFor("--language"), "ts");
  assert.equal(valueFor("--channel"), "stable");
  assert.equal(valueFor("--base-url"), "http://127.0.0.1:8100");
  assert.equal(valueFor("--telemetry"), "off");

  // The summary stack mirrors the forwarded flags for JSON consumers.
  assert.equal(parsed.stack.preset, "mobile-app");
  assert.equal(parsed.stack.tracker, "linear");
  assert.equal(parsed.stack.ci, "gh-actions");
  assert.equal(parsed.stack.language, "ts");
  assert.equal(parsed.stack.channel, "stable");
  assert.equal(parsed.stack.base_url, "http://127.0.0.1:8100");
  assert.deepEqual(parsed.stack.agents, ["cursor", "codex"]);
  assert.equal(parsed.stack.copy_rules, true);
  assert.equal(parsed.stack.bootstrap, true);
});

test("shipctl new --no-bootstrap --no-copy-rules drops those flags", () => {
  const parent = mktmp();
  const r = runNew(
    [
      "demo-optout",
      "--preset",
      "adoption-minimum",
      "--agents",
      "cursor",
      "--no-bootstrap",
      "--no-copy-rules",
      "--dry-run",
      "--json",
    ],
    { cwd: parent },
  );
  assert.equal(r.status, 0, r.stderr || r.stdout);
  const parsed = JSON.parse(r.stdout);
  const argv = parsed.init_argv;
  assert.ok(!argv.includes("--copy-rules"), "must honour --no-copy-rules");
  assert.ok(!argv.includes("--bootstrap"), "must honour --no-bootstrap");
  assert.equal(parsed.stack.copy_rules, false);
  assert.equal(parsed.stack.bootstrap, false);
});
