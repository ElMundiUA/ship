#!/usr/bin/env node
/**
 * Single source of truth for the Ship release version.
 *
 *   VERSION                              (semver, single line — source of truth)
 *     │
 *     ├─ package.json                    (root)
 *     ├─ apps/landing/package.json
 *     ├─ apps/console/package.json
 *     ├─ packages/cli/package.json
 *     ├─ e2e/package.json
 *     └─ apps/backend/app/main.py        (FastAPI(title=…, version="…"))
 *
 * Two subcommands, both safe to run repeatedly:
 *
 *   node tools/scripts/version.mjs sync
 *     Read VERSION and write the same value into every tracked file.
 *     Exits 0 if all files are already in sync; exits 1 if any file changed.
 *     Used by CI to fail PRs that forgot to bump.
 *
 *   node tools/scripts/version.mjs bump <major|minor|patch|x.y.z>
 *     Bump VERSION, then run sync. Prints the new version on stdout so a
 *     release script can `git tag v$(tools/scripts/version.mjs bump patch)`.
 *
 * Convention: a single git tag `v<x.y.z>` is the release marker. The CLI
 * publish workflow (and any future backend image / landing build) keys off it.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// ``__dirname`` = ``<repo>/tools/scripts/`` after the monorepo refactor.
const repoRoot = resolve(__dirname, "..", "..");

const VERSION_FILE = join(repoRoot, "VERSION");

const SEMVER_RE = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;

/** All files that must carry the canonical version, with how to read/write each one. */
const TARGETS = [
  jsonTarget("package.json", "version"),
  jsonTarget("apps/landing/package.json", "version"),
  jsonTarget("apps/console/package.json", "version"),
  jsonTarget("packages/cli/package.json", "version"),
  jsonTarget("e2e/package.json", "version"),
  pythonFastApiTarget("apps/backend/app/main.py"),
];

function readVersion() {
  const raw = readFileSync(VERSION_FILE, "utf-8").trim();
  if (!SEMVER_RE.test(raw)) {
    throw new Error(`VERSION file does not contain a valid semver: ${JSON.stringify(raw)}`);
  }
  return raw;
}

function writeVersion(next) {
  writeFileSync(VERSION_FILE, `${next}\n`, "utf-8");
}

/** Bump one piece of a semver string. */
function bumpSemver(current, kind) {
  if (SEMVER_RE.test(kind) && !["major", "minor", "patch"].includes(kind)) {
    return kind;
  }
  const m = current.match(/^(\d+)\.(\d+)\.(\d+)/);
  if (!m) throw new Error(`Cannot bump non-semver value: ${current}`);
  let [maj, min, pat] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (kind === "major") return `${maj + 1}.0.0`;
  if (kind === "minor") return `${maj}.${min + 1}.0`;
  if (kind === "patch") return `${maj}.${min}.${pat + 1}`;
  throw new Error(`Unknown bump kind: ${kind}. Use major | minor | patch | x.y.z`);
}

/** ---------- Target helpers ---------- */

function jsonTarget(relPath, key) {
  const path = join(repoRoot, relPath);
  return {
    label: relPath,
    read() {
      const text = readFileSync(path, "utf-8");
      const data = JSON.parse(text);
      return data[key];
    },
    write(next) {
      const text = readFileSync(path, "utf-8");
      const data = JSON.parse(text);
      if (data[key] === next) return false;
      data[key] = next;
      const trailingNewline = text.endsWith("\n") ? "\n" : "";
      writeFileSync(path, `${JSON.stringify(data, null, 2)}${trailingNewline}`, "utf-8");
      return true;
    },
  };
}

function pythonFastApiTarget(relPath) {
  const path = join(repoRoot, relPath);
  /* Match: app = FastAPI(title="…", version="X.Y.Z")  — keep arg order. */
  const RE = /(FastAPI\([^)]*?\bversion\s*=\s*")([^"]+)(")/m;
  return {
    label: relPath,
    read() {
      const text = readFileSync(path, "utf-8");
      const m = text.match(RE);
      if (!m) throw new Error(`No FastAPI(..., version="…") in ${relPath}`);
      return m[2];
    },
    write(next) {
      const text = readFileSync(path, "utf-8");
      const m = text.match(RE);
      if (!m) throw new Error(`No FastAPI(..., version="…") in ${relPath}`);
      if (m[2] === next) return false;
      const out = text.replace(RE, `$1${next}$3`);
      writeFileSync(path, out, "utf-8");
      return true;
    },
  };
}

/** ---------- Commands ---------- */

function syncCommand() {
  const version = readVersion();
  const drifted = [];
  const written = [];
  for (const t of TARGETS) {
    const current = t.read();
    if (current !== version) drifted.push({ label: t.label, current });
    if (t.write(version)) written.push(t.label);
  }
  if (written.length === 0) {
    console.log(`version sync: all ${TARGETS.length} targets already at ${version}`);
    return 0;
  }
  console.log(`version sync: ${version}`);
  for (const d of drifted) {
    console.log(`  ${d.label}: ${d.current} → ${version}`);
  }
  return drifted.length > 0 ? 1 : 0;
}

function bumpCommand(kind) {
  if (!kind) {
    console.error("Usage: node tools/scripts/version.mjs bump <major|minor|patch|x.y.z>");
    process.exit(2);
  }
  const current = readVersion();
  const next = bumpSemver(current, kind);
  writeVersion(next);
  for (const t of TARGETS) {
    t.write(next);
  }
  console.log(next);
  return 0;
}

function checkCommand() {
  const version = readVersion();
  const drifted = [];
  for (const t of TARGETS) {
    const current = t.read();
    if (current !== version) drifted.push({ label: t.label, current });
  }
  if (drifted.length === 0) {
    console.log(`version check: all ${TARGETS.length} targets at ${version}`);
    return 0;
  }
  console.error(`version check: drift detected (canonical = ${version})`);
  for (const d of drifted) {
    console.error(`  ${d.label}: ${d.current}`);
  }
  console.error(`Run: node tools/scripts/version.mjs sync`);
  return 1;
}

const cmd = process.argv[2];
const arg = process.argv[3];

let exit = 0;
switch (cmd) {
  case "sync":
    exit = syncCommand();
    break;
  case "bump":
    exit = bumpCommand(arg);
    break;
  case "check":
    exit = checkCommand();
    break;
  case "show":
  case undefined:
    console.log(readVersion());
    break;
  default:
    console.error(`Unknown command: ${cmd}\nUsage: tools/scripts/version.mjs [show|sync|check|bump <kind>]`);
    exit = 2;
}

process.exit(exit);
