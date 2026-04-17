import { fetchManifest } from "../../http.mjs";
import { listCached } from "../../cache/store.mjs";

export const id = "artifacts-up-to-date";
export const category = "network";
export const description = "Local cache matches latest manifest on the configured channel";

function newestVersion(versions) {
  if (!Array.isArray(versions) || !versions.length) return null;
  // Manifest entries are usually already sorted descending; treat the first as latest.
  return versions[0];
}

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  let cache;
  try {
    cache = listCached(ctx.cwd);
  } catch {
    cache = [];
  }
  if (!cache.length) {
    return { status: "skip", detail: "no cached artifacts to compare" };
  }

  const baseUrl = ctx.baseUrl
    || (ctx.config && ctx.config.api && ctx.config.api.base_url)
    || process.env.SHIP_API_BASE
    || "https://ship.elmundi.com";
  const channel = (ctx.config && ctx.config.api && ctx.config.api.channel) || "stable";

  let manifest;
  try {
    manifest = await fetchManifest(baseUrl, { channel });
  } catch (e) {
    return { status: "warn", detail: `manifest fetch failed: ${e.message}` };
  }

  const index = new Map();
  for (const m of manifest || []) {
    const k = `${m.kind}/${m.id}`;
    if (!index.has(k)) index.set(k, m);
  }

  const stale = [];
  for (const entry of cache) {
    const key = `${entry.kind}/${entry.id}`;
    const m = index.get(key);
    if (!m) continue;
    const latest = m.version || (m.versions ? newestVersion(m.versions) : null);
    if (latest && entry.version && latest !== entry.version) {
      stale.push({
        kind: entry.kind,
        id: entry.id,
        cached: entry.version,
        latest,
      });
    }
  }

  if (stale.length) {
    const summary = stale
      .slice(0, 3)
      .map((s) => `${s.kind}/${s.id} ${s.cached}→${s.latest}`)
      .join(", ");
    return {
      status: "warn",
      detail: `${stale.length} stale artifact(s): ${summary}${stale.length > 3 ? "…" : ""} — run 'shipctl sync'`,
      data: { stale, channel },
    };
  }
  return {
    status: "pass",
    detail: `all ${cache.length} cached entries are current on channel=${channel}`,
  };
}
