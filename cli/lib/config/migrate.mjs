/**
 * `shipctl migrate` — convert `.ship/config.yml` from v1 to v2.
 *
 * v2 introduces the `lanes` map, `agent.default/overrides`, and deprecates
 * the `workflow` artifact kind. The migration is deliberately conservative:
 *
 *   - Every v1 field we still recognise is copied verbatim, not rewritten.
 *   - `stack.agent.provider` is lifted into `agent.default.provider`; we
 *     leave `stack.agent` intact so rolling back to an old shipctl still
 *     finds the field where v1 expected it.
 *   - The legacy `lanes:` list-of-strings (a shape customers wrote by
 *     hand between 0.9.x and 0.11.x — not a formal v1 field but widely
 *     present on disk) is translated into the v2 `lanes:` map using the
 *     preset defaults table below.
 *   - Unknown keys survive at the top level; v2 validation emits a
 *     warning but does not drop them.
 *
 * The migration is idempotent: running it against a v2 config returns
 * the input untouched.
 */

import {
  CONFIG_SCHEMA_VERSION,
  DEFAULT_PROCESS_CONFIG,
  LEGACY_CONFIG_SCHEMA_VERSION,
} from "./schema.mjs";

/**
 * Default lane translations for the well-known v1 `lanes:` list entries.
 * Each entry maps the v1 string id to a v2 lane body. These were the
 * four lane ids the `monorepo`, `web-app`, and `api-backend` presets
 * bundled; anything outside this table is left for the caller to fill
 * in manually (the migrator warns and adds a stub).
 *
 * Keep these aligned with `artifacts/collections/preset-*` and with
 * RFC-0007 §"Lane-id reservations".
 */
const V1_LANE_DEFAULTS = Object.freeze({
  pr_review: {
    kind: "event",
    pattern: "flow-pr-self-review",
    on: "pull_request",
    permissions: { contents: "read", "pull-requests": "write" },
  },
  daily_standup: {
    kind: "schedule",
    pattern: "flow-daily-retro",
    cron: "0 9 * * 1-5",
  },
  tech_debt: {
    kind: "schedule",
    pattern: "flow-learning-capture",
    cron: "0 10 * * 1",
  },
  self_heal: {
    kind: "event",
    pattern: "op-workflow-self-heal",
    on: "workflow_run",
    when: { conclusion: "failure" },
    permissions: { contents: "read", actions: "read", "pull-requests": "write" },
  },
});

/**
 * @typedef {{
 *   migrated: boolean,
 *   config: object,
 *   warnings: string[],
 *   stub_lanes: string[],
 * }} MigrationResult
 */

/**
 * Migrate a parsed config object from v1 to v2. Returns a fresh config
 * object (input is not mutated) plus any non-fatal warnings.
 *
 * @param {object} input
 * @returns {MigrationResult}
 */
export function migrateV1ToV2(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("migrate: config must be a mapping");
  }

  if (input.version === CONFIG_SCHEMA_VERSION) {
    return {
      migrated: false,
      config: input,
      warnings: ["config already at v2; nothing to do"],
      stub_lanes: [],
    };
  }
  if (input.version !== LEGACY_CONFIG_SCHEMA_VERSION) {
    throw new Error(
      `migrate: unsupported source version ${JSON.stringify(input.version)}; only v${LEGACY_CONFIG_SCHEMA_VERSION} is supported`,
    );
  }

  /* Deep-clone first — never touch the caller's object. YAML parse
   * output is pure JSON so JSON round-trip is safe. */
  const src = JSON.parse(JSON.stringify(input));
  const warnings = [];
  const stubLanes = [];

  const out = {
    version: CONFIG_SCHEMA_VERSION,
    /* Bump the hard floor to the release that introduces v2. Anyone
     * stuck on <0.12 will fail loudly on read instead of silently
     * pretending v2 is v1. */
    shipctl_min: bumpFloor(src.shipctl_min, "0.12.0"),
  };

  /* api / stack / cache / telemetry / artifacts survive verbatim — v2
   * only added new top-level siblings. Preserve unknown keys too so a
   * future field added by a newer shipctl on the same config doesn't
   * get eaten by an older migrator. */
  for (const k of Object.keys(src)) {
    if (k === "version" || k === "shipctl_min") continue;
    if (k === "lanes") continue; /* handled below */
    out[k] = src[k];
  }

  /* agent.default / agent.overrides */
  out.agent = out.agent && typeof out.agent === "object" ? out.agent : {};
  if (!out.agent.default || typeof out.agent.default !== "object") {
    out.agent.default = { provider: null };
  }
  if (!out.agent.overrides || typeof out.agent.overrides !== "object") {
    out.agent.overrides = {};
  }
  /* Lift stack.agent.provider into agent.default.provider if unset.
   * We intentionally leave the original value in place so v1 readers
   * keep working; v2 readers prefer agent.default.provider anyway. */
  const liftedProvider = src.stack?.agent?.provider ?? null;
  if (liftedProvider && !out.agent.default.provider) {
    out.agent.default.provider = liftedProvider;
  }

  if (!out.process || typeof out.process !== "object" || Array.isArray(out.process)) {
    out.process = cloneDefault(DEFAULT_PROCESS_CONFIG());
  }

  /* lanes: translate from the legacy list-of-strings shape. */
  out.lanes = {};
  const srcLanes = src.lanes;
  if (Array.isArray(srcLanes)) {
    for (const laneId of srcLanes) {
      if (typeof laneId !== "string") {
        warnings.push(`lanes: skipped non-string entry ${JSON.stringify(laneId)}`);
        continue;
      }
      const normalised = laneId.trim();
      if (!normalised) continue;
      const def = V1_LANE_DEFAULTS[normalised];
      if (def) {
        out.lanes[normalised] = cloneDefault(def);
      } else {
        /* Unknown v1 lane — emit a stub so the customer sees exactly
         * which fields need attention on the next `shipctl doctor`. */
        out.lanes[normalised] = {
          kind: "schedule",
          pattern: `TODO-pattern-for-${normalised}`,
          cron: "TODO",
        };
        stubLanes.push(normalised);
        warnings.push(
          `lanes.${normalised}: no preset mapping; wrote a stub (fill in kind/pattern/cron before shipping)`,
        );
      }
    }
  } else if (srcLanes && typeof srcLanes === "object") {
    /* Already a map (e.g. someone hand-edited partway). Copy as-is;
     * the v2 validator will flag any malformed lanes on the next
     * `shipctl doctor` or write. */
    out.lanes = JSON.parse(JSON.stringify(srcLanes));
  } else if (srcLanes !== undefined) {
    warnings.push(
      `lanes: unexpected v1 shape ${typeof srcLanes}; dropped. Add lanes manually or rerun 'shipctl init'.`,
    );
  }

  return {
    migrated: true,
    config: out,
    warnings,
    stub_lanes: stubLanes,
  };
}

function cloneDefault(laneBody) {
  return JSON.parse(JSON.stringify(laneBody));
}

/**
 * Parse a semver-ish "X.Y.Z" and return the higher of the current floor
 * and the minimum the caller wants. If `current` is not a string or
 * doesn't parse, we fall back to the minimum unconditionally — an
 * unreadable floor is no floor.
 *
 * @param {unknown} current
 * @param {string} minimum
 * @returns {string}
 */
function bumpFloor(current, minimum) {
  if (typeof current !== "string") return minimum;
  const cur = parseSemver(current);
  const min = parseSemver(minimum);
  if (!cur || !min) return minimum;
  return compareSemver(cur, min) >= 0 ? current : minimum;
}

function parseSemver(s) {
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(String(s).trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function compareSemver(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}
