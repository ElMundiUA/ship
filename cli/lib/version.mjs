/**
 * Read the CLI version straight from the published package.json so we never
 * end up with a hardcoded constant that drifts from npm. The value is also
 * the canonical Ship release version (kept in sync with the root VERSION
 * file by `scripts/version.mjs` — see top-level README "Versioning").
 *
 * Used by:
 *   - `shipctl --version` / `shipctl -v` / `shipctl version`
 *   - the `User-Agent` header on every outbound HTTP request, so the
 *     methodology API can correlate adoption metrics with the client release.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgPath = join(__dirname, "..", "package.json");

let cached = null;

export function getCliVersion() {
  if (cached) return cached;
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    cached = String(pkg.version || "0.0.0-unknown");
  } catch {
    cached = "0.0.0-unknown";
  }
  return cached;
}

export function getUserAgent() {
  return `shipctl/${getCliVersion()} (+https://github.com/ElMundiUA/ship)`;
}
