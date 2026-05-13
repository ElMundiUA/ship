#!/usr/bin/env node
/**
 * Fail if this PR touches any seed-bundle source material without
 * also bumping ``BUNDLE_VERSION`` in ``apps/backend/app/services/seed_bundle.py``.
 *
 * Why: ``BUNDLE_VERSION`` is the drift signal the Console uses to
 * show "Update available →" on every previously-seeded repo card.
 * If we silently change what ``install_bundle`` / ``wizard_seed``
 * emit without bumping the constant, existing tenants never get
 * prompted to re-seed, and their repos quietly rot against the
 * new contract.
 *
 * Usage:
 *   node tools/scripts/bundle-version-check.mjs [--base <ref>]
 *
 *   --base <ref>   Base revision to diff against. Defaults to
 *                  ``origin/main``. In GitHub Actions we pass the
 *                  PR's base SHA directly.
 *
 * Exit codes:
 *   0 — no bundle sources changed, or constant was bumped correctly
 *   1 — sources changed but constant did not
 *   2 — usage / environment problem
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
// ``__dirname`` = ``<repo>/tools/scripts/`` after the monorepo refactor.
const repoRoot = resolve(__dirname, "..", "..");

// Files that feed into the bundle a tenant receives on
// ``install_bundle`` / ``wizard_seed``. Keep this list in lockstep
// with ``compose_seed_files`` + ``preset_bundle_files`` reads.
//
// Strings are prefixes — any change under the prefix triggers.
const BUNDLE_SOURCE_PATHS = [
  "apps/backend/app/services/seed_bundle.py",
  "apps/backend/app/services/catalog.py",
  "apps/backend/app/services/lane_recipes.py",
  "apps/backend/app/services/tracker_fsm.py",
  "apps/backend/app/services/starter_workflows.py",
  "apps/backend/app/resources/starter_workflows/",
  "artifacts/knowledge-starters/",
];

const CONSTANT_FILE = "apps/backend/app/services/seed_bundle.py";
// Accept either ``BUNDLE_VERSION: int = 7`` or ``BUNDLE_VERSION: str = "0.7"``
// (current spelling). Comparison runs through ``Number()`` so dotted SemVer-ish
// strings like ``"0.7"`` order correctly against the prior baseline.
const CONSTANT_RE = /^BUNDLE_VERSION\s*:\s*\w+\s*=\s*"?([\d.]+)"?/m;

function parseArgs(argv) {
  const out = { base: "origin/main" };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--base") {
      out.base = argv[i + 1];
      i += 1;
    } else {
      console.error(`Unknown arg: ${a}`);
      process.exit(2);
    }
  }
  if (!out.base) {
    console.error("--base is required (or leave blank for origin/main)");
    process.exit(2);
  }
  return out;
}

function git(args) {
  return execFileSync("git", args, {
    cwd: repoRoot,
    encoding: "utf-8",
  }).trim();
}

function changedFilesSince(base) {
  const output = git(["diff", "--name-only", `${base}...HEAD`]);
  if (!output) return [];
  return output.split("\n").filter(Boolean);
}

function fileTouchesBundle(path) {
  return BUNDLE_SOURCE_PATHS.some((prefix) =>
    prefix.endsWith("/") ? path.startsWith(prefix) : path === prefix,
  );
}

function readBundleVersion(ref) {
  // ``ref === null`` means "current working tree".
  const text =
    ref === null
      ? readFileSync(join(repoRoot, CONSTANT_FILE), "utf-8")
      : git(["show", `${ref}:${CONSTANT_FILE}`]);
  const match = text.match(CONSTANT_RE);
  if (!match) {
    throw new Error(
      `Could not find BUNDLE_VERSION in ${CONSTANT_FILE}${ref ? `@${ref}` : ""}`,
    );
  }
  return Number(match[1]);
}

function main() {
  const { base } = parseArgs(process.argv.slice(2));

  const changed = changedFilesSince(base);
  const bundleTouched = changed.filter(fileTouchesBundle);

  if (bundleTouched.length === 0) {
    console.log(`bundle-version-check: no seed-bundle sources touched (base ${base}).`);
    return 0;
  }

  console.log("bundle-version-check: seed-bundle sources changed:");
  for (const f of bundleTouched) console.log(`  - ${f}`);

  const head = readBundleVersion(null);
  let baseline;
  try {
    baseline = readBundleVersion(base);
  } catch (err) {
    console.error(`Failed to read baseline BUNDLE_VERSION: ${err.message}`);
    console.error(
      "If this is a fresh branch that invents the constant, make sure base ref is fetched.",
    );
    return 2;
  }

  if (head > baseline) {
    console.log(
      `bundle-version-check: BUNDLE_VERSION bumped ${baseline} → ${head}. OK.`,
    );
    return 0;
  }

  console.error("");
  console.error(
    `bundle-version-check: FAIL — this PR changes seed-bundle sources but keeps BUNDLE_VERSION at ${head}.`,
  );
  console.error(
    `Bump the constant in ${CONSTANT_FILE} so the Console can show every existing tenant an "Upgrade →" CTA.`,
  );
  console.error("");
  console.error("To bypass intentionally (e.g. a doc-only change to one of the tracked files),");
  console.error("split the diff so the seed-bundle source file isn't touched in this PR, or");
  console.error("land the change together with a BUNDLE_VERSION bump in the same commit.");
  return 1;
}

process.exit(main());
