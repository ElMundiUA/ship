import fs from "node:fs";
import path from "node:path";
import { KNOWN_AGENTS } from "../../detect.mjs";
import { listCached, readCachedFrontMatter } from "../../cache/store.mjs";

export const id = "rules-markers";
export const category = "local";
export const description = "Agent rule files contain current artifacts-protocol markers";

const RULE_MARKER = "<!-- ship-cli: artifacts-protocol v1 -->";
const FOOTER_PREFIX = "<!-- ship-cli: installed-from ";
const FOOTER_SUFFIX = " -->";

function readFooter(body) {
  const idx = body.lastIndexOf(FOOTER_PREFIX);
  if (idx < 0) return null;
  const end = body.indexOf(FOOTER_SUFFIX, idx);
  if (end < 0) return null;
  return body.slice(idx + FOOTER_PREFIX.length, end).trim();
}

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const agents = (ctx.config && ctx.config.stack && Array.isArray(ctx.config.stack.agents))
    ? ctx.config.stack.agents
    : [];
  if (!agents.length) {
    return { status: "skip", detail: "stack.agents is empty" };
  }

  let cache = [];
  try {
    cache = listCached(ctx.cwd);
  } catch {
    cache = [];
  }

  const rows = [];
  let hasFail = false;
  let hasWarn = false;

  for (const agent of agents) {
    const spec = KNOWN_AGENTS[agent];
    // Prefer the install_target declared by the cached agent-rules artifact;
    // fall back to the hardcoded KNOWN_AGENTS mapping for graceful
    // degradation when the cache hasn't been populated yet (RFC-0004).
    let rel = null;
    let cachedFm = null;
    try {
      cachedFm = readCachedFrontMatter(ctx.cwd, "collection", `agent-rules-${agent}`);
    } catch {
      cachedFm = null;
    }
    if (!cachedFm) {
      rows.push({
        agent,
        status: "warn",
        detail: `no cached agent-rules-${agent}; run shipctl sync`,
      });
      hasWarn = true;
      continue;
    }
    const installTarget = cachedFm.fm && typeof cachedFm.fm.install_target === "string"
      ? cachedFm.fm.install_target.trim()
      : "";
    if (installTarget) {
      rel = installTarget;
    } else if (spec) {
      rel = path.join(...spec.targetRel);
    } else {
      rows.push({ agent, status: "warn", detail: `unknown agent id '${agent}' (no install_target and no KNOWN_AGENTS entry)` });
      hasWarn = true;
      continue;
    }

    const abs = path.join(ctx.cwd, rel);
    if (!fs.existsSync(abs)) {
      rows.push({ agent, status: "fail", detail: `missing rule file ${rel}` });
      hasFail = true;
      continue;
    }
    const body = fs.readFileSync(abs, "utf8");
    if (!body.includes(RULE_MARKER)) {
      rows.push({ agent, status: "fail", detail: `${rel} has no '${RULE_MARKER}' marker` });
      hasFail = true;
      continue;
    }
    const footer = readFooter(body);
    if (!footer) {
      rows.push({ agent, status: "fail", detail: `${rel} has no 'installed-from' footer` });
      hasFail = true;
      continue;
    }

    // footer looks like "collection/agent-rules-cursor@1.0.1"
    const m = /^collection\/agent-rules-[a-z0-9-]+@([0-9A-Za-z.\-+]+)$/.exec(footer);
    if (!m) {
      rows.push({ agent, status: "warn", detail: `${rel} footer is '${footer}' (non-standard format)` });
      hasWarn = true;
      continue;
    }
    const installedVersion = m[1];
    const cached = cache.find(
      (c) => c.kind === "collection" && c.id === `agent-rules-${agent}`,
    );
    if (cached && cached.version && cached.version !== installedVersion) {
      rows.push({
        agent,
        status: "warn",
        detail: `${rel} footer @${installedVersion}, cache has @${cached.version} — run 'shipctl init --copy-rules'`,
      });
      hasWarn = true;
    } else {
      rows.push({ agent, status: "pass", detail: `${rel} @${installedVersion}` });
    }
  }

  const overall = hasFail ? "fail" : hasWarn ? "warn" : "pass";
  const summary = rows
    .filter((r) => r.status !== "pass")
    .map((r) => `${r.agent}: ${r.detail}`)
    .join("; ");
  const detail = overall === "pass"
    ? `all ${rows.length} agent rule files have correct markers`
    : summary || `${rows.length} agent rule files inspected`;
  return { status: overall, detail, data: { rows } };
}
