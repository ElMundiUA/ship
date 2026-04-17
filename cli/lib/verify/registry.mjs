/**
 * Verify registry: central import point for all Ship verify checks.
 *
 * @typedef {Object} CheckContext
 * @property {string} cwd
 * @property {object|null} config       Parsed .ship/config.yml (or null if missing).
 * @property {object|null} [inventory]  Optional .ship/inventory.json snapshot.
 * @property {string} [baseUrl]         Effective methodology API base URL.
 * @property {(msg:string)=>void} [logger]
 *
 * @typedef {"pass"|"warn"|"fail"|"skip"} CheckStatus
 *
 * @typedef {Object} CheckResult
 * @property {CheckStatus} status
 * @property {string} detail
 * @property {object} [data]
 *
 * @typedef {Object} Check
 * @property {string} id
 * @property {"local"|"config"|"network"} category
 * @property {string} description
 * @property {(ctx:CheckContext)=>Promise<CheckResult>} run
 */

import * as configPresent from "./checks/config-present.mjs";
import * as gitignoreCache from "./checks/gitignore-cache.mjs";
import * as rulesMarkers from "./checks/rules-markers.mjs";
import * as cacheIntegrity from "./checks/cache-integrity.mjs";
import * as bootstrapFiles from "./checks/bootstrap-files.mjs";
import * as stackEnums from "./checks/stack-enums.mjs";
import * as agentsOnDisk from "./checks/agents-on-disk.mjs";
import * as apiReachable from "./checks/api-reachable.mjs";
import * as artifactsUpToDate from "./checks/artifacts-up-to-date.mjs";
import * as trackerLabels from "./checks/tracker-labels.mjs";
import * as ciSecrets from "./checks/ci-secrets.mjs";

/**
 * Ordered list of checks. Order governs how they appear in `shipctl verify`
 * output; within a category we keep a stable human-friendly grouping.
 * @type {Check[]}
 */
const CHECKS = [
  configPresent,
  gitignoreCache,
  stackEnums,
  rulesMarkers,
  cacheIntegrity,
  bootstrapFiles,
  agentsOnDisk,
  apiReachable,
  artifactsUpToDate,
  trackerLabels,
  ciSecrets,
];

export function allChecks() {
  return CHECKS.slice();
}

/**
 * @param {CheckContext} ctx
 * @param {{filter?:string[]|null, noNetwork?:boolean}} [opts]
 * @returns {Promise<Array<{id:string, category:string, description:string, status:CheckStatus, detail:string, data?:object, duration_ms:number}>>}
 */
export async function runChecks(ctx, opts = {}) {
  const { filter = null, noNetwork = false } = opts;
  const wantSet = filter && filter.length
    ? new Set(filter.map((s) => String(s).trim()).filter(Boolean))
    : null;

  const out = [];
  for (const check of CHECKS) {
    if (wantSet && !wantSet.has(check.id)) continue;
    if (noNetwork && check.category === "network") {
      out.push({
        id: check.id,
        category: check.category,
        description: check.description,
        status: "skip",
        detail: "skipped (--no-network)",
        duration_ms: 0,
      });
      continue;
    }
    const started = Date.now();
    let res;
    try {
      res = await check.run(ctx);
    } catch (e) {
      res = {
        status: "fail",
        detail: `check threw: ${e instanceof Error ? e.message : String(e)}`,
      };
    }
    out.push({
      id: check.id,
      category: check.category,
      description: check.description,
      status: res.status || "fail",
      detail: res.detail || "",
      data: res.data,
      duration_ms: Date.now() - started,
    });
  }
  return out;
}

/**
 * Summarise a list of CheckResult rows.
 */
export function summarize(rows) {
  const summary = { total: rows.length, pass: 0, warn: 0, fail: 0, skip: 0 };
  for (const r of rows) {
    if (r.status === "pass") summary.pass += 1;
    else if (r.status === "warn") summary.warn += 1;
    else if (r.status === "fail") summary.fail += 1;
    else summary.skip += 1;
  }
  return summary;
}
