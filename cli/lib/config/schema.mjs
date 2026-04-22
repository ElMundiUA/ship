import { KNOWN_AGENTS } from "../detect.mjs";

/* Historical schema used by every released shipctl through 0.11.x. We
 * keep validating it in parallel with v2 so customers who haven't run
 * `shipctl migrate` see clear warnings instead of silent failures. */
export const LEGACY_CONFIG_SCHEMA_VERSION = 1;

/* RFC-0007 lanes-as-config. Introduced alongside `shipctl run`. Clients
 * that understand only v1 will refuse to read v2 and print a shipctl
 * upgrade hint; clients that understand v2 accept v1 with a deprecation
 * warning and suggest `shipctl migrate`. */
export const CONFIG_SCHEMA_VERSION = 2;

export const SUPPORTED_CONFIG_VERSIONS = Object.freeze([
  LEGACY_CONFIG_SCHEMA_VERSION,
  CONFIG_SCHEMA_VERSION,
]);

/* Lane kinds accepted in v2. `once` ships today end-to-end; `event` and
 * `schedule` are parsed + validated but `shipctl run` emits a "not-yet
 * implemented" exit-0 no-op for them until Phase 3 wires the reusable
 * workflow. Keep this union tight — any new kind requires an RFC
 * amendment. */
export const LANE_KINDS = Object.freeze(["once", "event", "schedule"]);

export const LANE_EVENT_TYPES = Object.freeze([
  "pull_request",
  "push",
  "workflow_run",
  "deployment_status",
]);

export const LANE_IDEMPOTENCY_STORES = Object.freeze(["file", "backend"]);
export const LANE_IDEMPOTENCY_RESET_ON = Object.freeze(["version-change", "manual"]);

/* RFC-0008 C3.2 — fan-out strategy for multi-pattern lanes.
 *
 *   matrix      — GitHub Actions matrix: one runner per pattern, parallel.
 *   sequential  — Single runner, `shipctl run` iterates patterns in order.
 *   concurrent  — Single runner, `shipctl run` spawns subprocesses in parallel.
 *
 * Meaningful only when ``patterns.length > 1``; single-pattern lanes ignore
 * it (the linter warns if it's set on a single-pattern lane). Default:
 * ``matrix``. */
export const LANE_FANOUT_MODES = Object.freeze([
  "matrix",
  "sequential",
  "concurrent",
]);
export const LANE_FANOUT_DEFAULT = "matrix";

/* Lane ids travel into file paths (`.ship/state/<key>.json`), workflow
 * file names (`.github/workflows/ship-<lane>.yml`), and env vars, so
 * restrict them conservatively: ASCII lowercase, digits, dash, underscore. */
export const LANE_ID_REGEX = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const IDEMPOTENCY_KEY_REGEX = /^[a-z0-9][a-z0-9_.-]{0,127}$/;
/* Coarse sanity check for 5-field crons: we're not a cron parser, but
 * anything that isn't whitespace-separated 5 tokens is almost certainly
 * a typo and worth rejecting up front. */
const CRON_5_FIELD_REGEX = /^\s*(\S+\s+){4}\S+\s*$/;

export const TRACKERS = Object.freeze([
  "linear",
  "jira",
  "github-issues",
  "azure-boards",
  "clickup",
  "spreadsheet",
  "none",
]);

export const CIS = Object.freeze([
  "gh-actions",
  "gitlab-ci",
  "buildkite",
  "circleci",
  "azure-pipelines",
  "jenkins",
  "manual",
]);

export const LANGUAGES = Object.freeze([
  "ts",
  "js",
  "py",
  "go",
  "rust",
  "java",
  "kotlin",
  "swift",
  "dart",
  "multi",
]);

export const PRESETS = Object.freeze([
  "web-app",
  "api-backend",
  "mobile-app",
  "cli",
  "monorepo",
  "adoption-minimum",
]);

export const CHANNELS = Object.freeze(["stable", "edge"]);

export const KINDS = Object.freeze(["pattern", "tool", "collection", "doc"]);

export const AGENT_IDS = Object.freeze(Object.keys(KNOWN_AGENTS));

export const PIN_KEY_REGEX = /^(pattern|tool|collection|doc)\/[a-zA-Z0-9_\-\.\/]+$/;

export const UUID_V4_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const SEMVER_OR_RANGE_REGEX =
  /^(\^|~|>=|<=|>|<|=)?\s*\d+(\.\d+){0,2}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/;

/**
 * Produce a fresh, independent default v1 config (all nested objects are new).
 *
 * Kept for the legacy `shipctl config init` path and any callers that
 * still dogfood v1. New installs go through DEFAULT_CONFIG_V2 below.
 */
export function DEFAULT_CONFIG_V1() {
  return {
    version: LEGACY_CONFIG_SCHEMA_VERSION,
    shipctl_min: "0.11.2",
    api: {
      base_url: "https://ship.elmundi.com",
      channel: "stable",
      ttl_hours: 24,
      offline_ok: true,
    },
    stack: {
      tracker: "none",
      ci: "manual",
      agents: [],
      language: "multi",
      preset: "adoption-minimum",
    },
    artifacts: {
      pins: {},
      auto_update: true,
    },
    cache: {
      vcs_tracked: false,
    },
    telemetry: {
      share: false,
      anonymous_id: null,
      scope: {
        artifact_usage: true,
        improvement_drafts: true,
        errors: false,
      },
    },
  };
}

/**
 * Produce a fresh, independent default v2 config. `lanes` is empty — the
 * install flow (`shipctl init`, presets) seeds it with per-preset lanes.
 */
export function DEFAULT_CONFIG_V2() {
  const v1 = DEFAULT_CONFIG_V1();
  return {
    version: CONFIG_SCHEMA_VERSION,
    shipctl_min: "0.12.0",
    api: v1.api,
    stack: v1.stack,
    agent: {
      default: { provider: null },
      overrides: {},
    },
    lanes: {},
    artifacts: v1.artifacts,
    cache: v1.cache,
    telemetry: v1.telemetry,
  };
}

/**
 * Back-compat alias. Pre-existing callers expect DEFAULT_CONFIG() to
 * return the current schema's shape; default to v2 so new installs
 * benefit from lanes.
 */
export function DEFAULT_CONFIG() {
  return DEFAULT_CONFIG_V2();
}

/** @typedef {{ok:true,config:object,warnings:string[]}|{ok:false,errors:string[],warnings:string[]}} ValidationResult */

const KNOWN_TOP_LEVEL_V1 = new Set([
  "version",
  "shipctl_min",
  "api",
  "stack",
  "artifacts",
  "cache",
  "telemetry",
]);

const KNOWN_TOP_LEVEL_V2 = new Set([
  "version",
  "shipctl_min",
  "api",
  "stack",
  "agent",
  "lanes",
  "artifacts",
  "cache",
  "telemetry",
]);

/* Back-compat export — keep the old symbol alive so external importers
 * (if any) don't break silently. Point it at the v2 set since new
 * callers should assume v2. */
const KNOWN_TOP_LEVEL = KNOWN_TOP_LEVEL_V2;

const KNOWN_API = new Set(["base_url", "channel", "ttl_hours", "offline_ok"]);
const KNOWN_STACK = new Set(["tracker", "ci", "agents", "agent", "language", "preset"]);
const KNOWN_STACK_AGENT = new Set(["provider"]);
const KNOWN_ARTIFACTS = new Set(["pins", "auto_update"]);
const KNOWN_CACHE = new Set(["vcs_tracked"]);
const KNOWN_TELEMETRY = new Set(["share", "anonymous_id", "scope"]);
const KNOWN_TELEMETRY_SCOPE = new Set(["artifact_usage", "improvement_drafts", "errors"]);

function isPlainObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function pushUnknownKeyWarnings(obj, allowed, prefix, warnings) {
  if (!isPlainObject(obj)) return;
  for (const k of Object.keys(obj)) {
    if (!allowed.has(k)) {
      warnings.push(`${prefix}.${k}: unknown key (ignored, preserved on write)`);
    }
  }
}

/**
 * Validate the common shared portion of v1 and v2. Mutates `errors` and
 * `warnings` in place. The per-version wrappers below layer on
 * schema-specific validators on top of this foundation.
 *
 * @param {object} obj
 * @param {string[]} errors
 * @param {string[]} warnings
 */
function validateSharedSections(obj, errors, warnings) {
  const api = obj.api;
  if (!isPlainObject(api)) {
    errors.push("api: must be an object");
  } else {
    pushUnknownKeyWarnings(api, KNOWN_API, "api", warnings);
    if (typeof api.base_url !== "string") {
      errors.push("api.base_url: must be a string URL");
    } else {
      try {
        new URL(api.base_url);
      } catch {
        errors.push(`api.base_url: not a valid URL (${api.base_url})`);
      }
    }
    if (api.channel !== undefined && !CHANNELS.includes(api.channel)) {
      errors.push(
        `api.channel: ${JSON.stringify(api.channel)} is not valid. Expected one of: ${CHANNELS.join(", ")}`,
      );
    }
    if (api.ttl_hours !== undefined) {
      if (typeof api.ttl_hours !== "number" || !Number.isFinite(api.ttl_hours) || api.ttl_hours < 0) {
        errors.push("api.ttl_hours: must be a number ≥ 0");
      }
    }
    if (api.offline_ok !== undefined && typeof api.offline_ok !== "boolean") {
      errors.push("api.offline_ok: must be boolean");
    }
  }
}

/**
 * v2-specific validator for the `agent` and `lanes` blocks.
 *
 * @param {object} obj
 * @param {string[]} errors
 * @param {string[]} warnings
 */
function validateV2Lanes(obj, errors, warnings) {
  const agent = obj.agent;
  if (agent !== undefined) {
    if (!isPlainObject(agent)) {
      errors.push("agent: must be an object");
    } else {
      pushUnknownKeyWarnings(agent, new Set(["default", "overrides"]), "agent", warnings);
      if (agent.default !== undefined) {
        if (!isPlainObject(agent.default)) {
          errors.push("agent.default: must be an object");
        } else {
          pushUnknownKeyWarnings(agent.default, new Set(["provider"]), "agent.default", warnings);
          const p = agent.default.provider;
          if (p !== undefined && p !== null) {
            if (typeof p !== "string" || p.length < 1 || p.length > 64) {
              errors.push("agent.default.provider: must be a non-empty string (≤64 chars)");
            }
          }
        }
      }
      if (agent.overrides !== undefined) {
        if (!isPlainObject(agent.overrides)) {
          errors.push("agent.overrides: must be a map");
        } else {
          for (const [laneId, override] of Object.entries(agent.overrides)) {
            if (!LANE_ID_REGEX.test(laneId)) {
              errors.push(`agent.overrides[${JSON.stringify(laneId)}]: invalid lane id`);
              continue;
            }
            if (!isPlainObject(override)) {
              errors.push(`agent.overrides.${laneId}: must be an object`);
              continue;
            }
            const p = override.provider;
            if (p !== undefined && p !== null && (typeof p !== "string" || p.length < 1 || p.length > 64)) {
              errors.push(`agent.overrides.${laneId}.provider: must be a non-empty string (≤64 chars)`);
            }
          }
        }
      }
    }
  }

  const lanes = obj.lanes;
  if (lanes === undefined) {
    /* An empty lanes map is legal — a fresh repo that has only gone
     * through `shipctl migrate` has no automation wired yet, and that's
     * the right default for onboarding. */
    return;
  }
  if (!isPlainObject(lanes)) {
    errors.push("lanes: must be a map of lane-id → lane");
    return;
  }
  for (const [laneId, lane] of Object.entries(lanes)) {
    validateLane(laneId, lane, errors, warnings);
  }
}

const KNOWN_LANE_COMMON = new Set([
  "kind",
  "pattern",
  "patterns",
  "pattern_version",
  "fanout",
  "permissions",
  "runner",
  "timeout_minutes",
  "concurrency",
]);
const KNOWN_LANE_ONCE = new Set([...KNOWN_LANE_COMMON, "idempotency"]);
const KNOWN_LANE_EVENT = new Set([...KNOWN_LANE_COMMON, "on", "when"]);
const KNOWN_LANE_SCHEDULE = new Set([...KNOWN_LANE_COMMON, "cron", "cron_tz"]);

/**
 * @param {string} laneId
 * @param {any} lane
 * @param {string[]} errors
 * @param {string[]} warnings
 */
function validateLane(laneId, lane, errors, warnings) {
  if (!LANE_ID_REGEX.test(laneId)) {
    errors.push(
      `lanes[${JSON.stringify(laneId)}]: invalid id; expected /^[a-z0-9][a-z0-9_-]{0,63}$/`,
    );
    return;
  }
  if (!isPlainObject(lane)) {
    errors.push(`lanes.${laneId}: must be an object`);
    return;
  }

  const prefix = `lanes.${laneId}`;

  if (typeof lane.kind !== "string" || !LANE_KINDS.includes(lane.kind)) {
    errors.push(
      `${prefix}.kind: must be one of ${LANE_KINDS.join("|")}; got ${JSON.stringify(lane.kind)}`,
    );
  }
  // ``patterns: [ids]`` is the canonical multi-pattern form (RFC-0008 C3);
  // ``pattern: <id>`` is kept as the single-pattern alias so existing
  // configs keep working. Exactly one of the two must be present.
  const hasPatterns = Array.isArray(lane.patterns);
  const hasPattern = typeof lane.pattern === "string";
  if (hasPatterns && hasPattern) {
    errors.push(
      `${prefix}: use either 'pattern' (single) or 'patterns' (list), not both`,
    );
  } else if (hasPatterns) {
    if (lane.patterns.length < 1) {
      errors.push(`${prefix}.patterns: must contain at least one pattern id`);
    } else {
      for (let i = 0; i < lane.patterns.length; i += 1) {
        const p = lane.patterns[i];
        if (typeof p !== "string" || !p.trim()) {
          errors.push(
            `${prefix}.patterns[${i}]: must be a non-empty pattern id string`,
          );
        }
      }
    }
  } else if (hasPattern) {
    if (!lane.pattern.trim()) {
      errors.push(`${prefix}.pattern: must be a non-empty pattern id`);
    }
  } else {
    errors.push(
      `${prefix}: must declare 'pattern' (single) or 'patterns' (list)`,
    );
  }
  if (
    lane.pattern_version !== undefined &&
    (typeof lane.pattern_version !== "string" || !lane.pattern_version.trim())
  ) {
    errors.push(`${prefix}.pattern_version: must be a non-empty semver string when set`);
  }
  // RFC-0008 C3.2 — `fanout` picks how multi-pattern lanes execute.
  // Single-pattern lanes ignore it (it's a no-op for them); we emit a
  // warning rather than an error so schedule templates that set it
  // blindly remain portable across single/multi-pattern use.
  if (lane.fanout !== undefined) {
    if (typeof lane.fanout !== "string" || !LANE_FANOUT_MODES.includes(lane.fanout)) {
      errors.push(
        `${prefix}.fanout: must be one of ${LANE_FANOUT_MODES.join("|")}; got ${JSON.stringify(lane.fanout)}`,
      );
    } else if (hasPattern || (hasPatterns && lane.patterns.length < 2)) {
      warnings.push(
        `${prefix}.fanout: ignored for single-pattern lanes (has no effect unless 'patterns' has ≥2 entries)`,
      );
    }
  }
  if (lane.permissions !== undefined && !isPlainObject(lane.permissions)) {
    errors.push(`${prefix}.permissions: must be an object when set`);
  }
  if (lane.runner !== undefined && (typeof lane.runner !== "string" || !lane.runner.trim())) {
    errors.push(`${prefix}.runner: must be a non-empty string when set`);
  }
  if (lane.timeout_minutes !== undefined) {
    const n = lane.timeout_minutes;
    if (typeof n !== "number" || !Number.isFinite(n) || n < 1 || n > 6 * 60) {
      errors.push(`${prefix}.timeout_minutes: must be an integer between 1 and 360`);
    }
  }
  if (lane.concurrency !== undefined) {
    if (!isPlainObject(lane.concurrency)) {
      errors.push(`${prefix}.concurrency: must be an object`);
    } else {
      pushUnknownKeyWarnings(
        lane.concurrency,
        new Set(["group", "cancel_in_progress"]),
        `${prefix}.concurrency`,
        warnings,
      );
      if (typeof lane.concurrency.group !== "string" || !lane.concurrency.group.trim()) {
        errors.push(`${prefix}.concurrency.group: must be a non-empty string`);
      }
      if (
        lane.concurrency.cancel_in_progress !== undefined &&
        typeof lane.concurrency.cancel_in_progress !== "boolean"
      ) {
        errors.push(`${prefix}.concurrency.cancel_in_progress: must be boolean`);
      }
    }
  }

  switch (lane.kind) {
    case "once":
      pushUnknownKeyWarnings(lane, KNOWN_LANE_ONCE, prefix, warnings);
      validateLaneIdempotency(lane, prefix, errors, warnings);
      break;
    case "event":
      pushUnknownKeyWarnings(lane, KNOWN_LANE_EVENT, prefix, warnings);
      if (typeof lane.on !== "string" || !LANE_EVENT_TYPES.includes(lane.on)) {
        errors.push(
          `${prefix}.on: must be one of ${LANE_EVENT_TYPES.join("|")}; got ${JSON.stringify(lane.on)}`,
        );
      }
      if (lane.when !== undefined && !isPlainObject(lane.when)) {
        errors.push(`${prefix}.when: must be an object when set`);
      }
      break;
    case "schedule":
      pushUnknownKeyWarnings(lane, KNOWN_LANE_SCHEDULE, prefix, warnings);
      if (typeof lane.cron !== "string" || !CRON_5_FIELD_REGEX.test(lane.cron)) {
        errors.push(
          `${prefix}.cron: must be a 5-field cron expression; got ${JSON.stringify(lane.cron)}`,
        );
      }
      if (lane.cron_tz !== undefined && (typeof lane.cron_tz !== "string" || !lane.cron_tz.trim())) {
        errors.push(`${prefix}.cron_tz: must be a non-empty IANA tz string when set`);
      }
      break;
  }
}

function validateLaneIdempotency(lane, prefix, errors, warnings) {
  const idem = lane.idempotency;
  if (!isPlainObject(idem)) {
    errors.push(`${prefix}.idempotency: must be an object (kind=once requires it)`);
    return;
  }
  pushUnknownKeyWarnings(
    idem,
    new Set(["key", "store", "reset_on"]),
    `${prefix}.idempotency`,
    warnings,
  );
  if (typeof idem.key !== "string" || !IDEMPOTENCY_KEY_REGEX.test(idem.key)) {
    errors.push(
      `${prefix}.idempotency.key: must match /^[a-z0-9][a-z0-9_.-]{0,127}$/; got ${JSON.stringify(idem.key)}`,
    );
  }
  if (idem.store !== undefined && !LANE_IDEMPOTENCY_STORES.includes(idem.store)) {
    errors.push(
      `${prefix}.idempotency.store: must be one of ${LANE_IDEMPOTENCY_STORES.join("|")}`,
    );
  }
  if (idem.reset_on !== undefined && !LANE_IDEMPOTENCY_RESET_ON.includes(idem.reset_on)) {
    errors.push(
      `${prefix}.idempotency.reset_on: must be one of ${LANE_IDEMPOTENCY_RESET_ON.join("|")}`,
    );
  }
}

/**
 * @param {any} obj
 * @returns {ValidationResult}
 */
export function validateConfig(obj) {
  const errors = [];
  const warnings = [];

  if (!isPlainObject(obj)) {
    return { ok: false, errors: ["config must be a YAML mapping"], warnings };
  }

  if (!SUPPORTED_CONFIG_VERSIONS.includes(obj.version)) {
    errors.push(
      `version: unsupported; expected one of ${SUPPORTED_CONFIG_VERSIONS.join(", ")}, got ${JSON.stringify(obj.version)}`,
    );
    return { ok: false, errors, warnings };
  }

  const isV2 = obj.version === CONFIG_SCHEMA_VERSION;
  const topLevel = isV2 ? KNOWN_TOP_LEVEL_V2 : KNOWN_TOP_LEVEL_V1;
  pushUnknownKeyWarnings(obj, topLevel, "", warnings);

  if (!isV2) {
    warnings.push(
      `version: config is at v${obj.version}; run \`shipctl migrate\` to upgrade to v${CONFIG_SCHEMA_VERSION}`,
    );
  }

  validateSharedSections(obj, errors, warnings);
  if (isV2) {
    validateV2Lanes(obj, errors, warnings);
  }

  /* stack / artifacts / telemetry / cache share the same shape between
   * v1 and v2; api is already covered by validateSharedSections above. */

  const stack = obj.stack;
  if (!isPlainObject(stack)) {
    errors.push("stack: must be an object");
  } else {
    pushUnknownKeyWarnings(stack, KNOWN_STACK, "stack", warnings);
    if (stack.tracker !== undefined && !TRACKERS.includes(stack.tracker)) {
      errors.push(
        `stack.tracker: ${JSON.stringify(stack.tracker)} is not valid. Expected one of: ${TRACKERS.join(", ")}`,
      );
    }
    if (stack.ci !== undefined && !CIS.includes(stack.ci)) {
      errors.push(
        `stack.ci: ${JSON.stringify(stack.ci)} is not valid. Expected one of: ${CIS.join(", ")}`,
      );
    }
    if (stack.language !== undefined && !LANGUAGES.includes(stack.language)) {
      errors.push(
        `stack.language: ${JSON.stringify(stack.language)} is not valid. Expected one of: ${LANGUAGES.join(", ")}`,
      );
    }
    if (stack.preset !== undefined && !PRESETS.includes(stack.preset)) {
      errors.push(
        `stack.preset: ${JSON.stringify(stack.preset)} is not valid. Expected one of: ${PRESETS.join(", ")}`,
      );
    }
    if (stack.agents !== undefined) {
      if (!Array.isArray(stack.agents)) {
        errors.push("stack.agents: must be an array");
      } else {
        for (const a of stack.agents) {
          if (typeof a !== "string" || !AGENT_IDS.includes(a)) {
            errors.push(
              `stack.agents: ${JSON.stringify(a)} is not valid. Expected one of: ${AGENT_IDS.join(", ")}`,
            );
          }
        }
      }
    }
    if (stack.agent !== undefined) {
      if (!isPlainObject(stack.agent)) {
        errors.push("stack.agent: must be an object");
      } else {
        pushUnknownKeyWarnings(stack.agent, KNOWN_STACK_AGENT, "stack.agent", warnings);
        const p = stack.agent.provider;
        if (p !== undefined && p !== null) {
          if (typeof p !== "string" || p.length < 1 || p.length > 64) {
            errors.push(
              "stack.agent.provider: must be a non-empty string (≤64 chars), e.g. claude-code or cursor-cloud",
            );
          }
        }
      }
    }
  }

  const artifacts = obj.artifacts;
  if (artifacts !== undefined) {
    if (!isPlainObject(artifacts)) {
      errors.push("artifacts: must be an object");
    } else {
      pushUnknownKeyWarnings(artifacts, KNOWN_ARTIFACTS, "artifacts", warnings);
      if (artifacts.pins !== undefined) {
        if (!isPlainObject(artifacts.pins)) {
          errors.push("artifacts.pins: must be a map");
        } else {
          for (const [k, v] of Object.entries(artifacts.pins)) {
            if (!PIN_KEY_REGEX.test(k)) {
              errors.push(
                `artifacts.pins[${JSON.stringify(k)}]: invalid key; expected <kind>/<id> where kind∈{pattern,tool,collection,doc}`,
              );
            }
            if (typeof v !== "string" || !SEMVER_OR_RANGE_REGEX.test(v.trim())) {
              errors.push(
                `artifacts.pins[${JSON.stringify(k)}]: value must be a semver or range (got ${JSON.stringify(v)})`,
              );
            }
          }
        }
      }
      if (artifacts.auto_update !== undefined && typeof artifacts.auto_update !== "boolean") {
        errors.push("artifacts.auto_update: must be boolean");
      }
    }
  }

  const cache = obj.cache;
  if (cache !== undefined) {
    if (!isPlainObject(cache)) {
      errors.push("cache: must be an object");
    } else {
      pushUnknownKeyWarnings(cache, KNOWN_CACHE, "cache", warnings);
      if (cache.vcs_tracked !== undefined && typeof cache.vcs_tracked !== "boolean") {
        errors.push("cache.vcs_tracked: must be boolean");
      }
    }
  }

  const telemetry = obj.telemetry;
  if (telemetry !== undefined) {
    if (!isPlainObject(telemetry)) {
      errors.push("telemetry: must be an object");
    } else {
      pushUnknownKeyWarnings(telemetry, KNOWN_TELEMETRY, "telemetry", warnings);
      if (telemetry.share !== undefined && typeof telemetry.share !== "boolean") {
        errors.push("telemetry.share: must be boolean");
      }
      if (telemetry.share === true) {
        if (typeof telemetry.anonymous_id !== "string" || !UUID_V4_REGEX.test(telemetry.anonymous_id)) {
          errors.push(
            "telemetry.anonymous_id: required UUID v4 when telemetry.share=true",
          );
        }
      } else if (
        telemetry.anonymous_id !== undefined &&
        telemetry.anonymous_id !== null &&
        (typeof telemetry.anonymous_id !== "string" || !UUID_V4_REGEX.test(telemetry.anonymous_id))
      ) {
        errors.push(
          `telemetry.anonymous_id: ${JSON.stringify(telemetry.anonymous_id)} is not a valid UUID v4`,
        );
      }
      if (telemetry.scope !== undefined) {
        if (!isPlainObject(telemetry.scope)) {
          errors.push("telemetry.scope: must be an object");
        } else {
          pushUnknownKeyWarnings(telemetry.scope, KNOWN_TELEMETRY_SCOPE, "telemetry.scope", warnings);
          for (const k of KNOWN_TELEMETRY_SCOPE) {
            if (telemetry.scope[k] !== undefined && typeof telemetry.scope[k] !== "boolean") {
              errors.push(`telemetry.scope.${k}: must be boolean`);
            }
          }
        }
      }
    }
  }

  if (typeof obj.shipctl_min !== "undefined" && typeof obj.shipctl_min !== "string") {
    errors.push("shipctl_min: must be a semver string");
  }

  if (errors.length) return { ok: false, errors, warnings };
  return { ok: true, config: obj, warnings };
}

/**
 * Return the canonical list of pattern ids for a lane.
 *
 * Accepts both the canonical ``patterns: [ids]`` (RFC-0008) and the
 * legacy ``pattern: <id>`` single-string alias. Use this helper
 * everywhere that reads ``lane.pattern`` / ``lane.patterns`` so the
 * call-site never has to branch on the two shapes.
 *
 * For lanes that declared neither key (e.g. malformed config that
 * slipped past validateConfig), returns an empty list rather than
 * throwing — the caller already surfaced the validation error.
 *
 * @param {any} lane
 * @returns {string[]}
 */
export function lanePatterns(lane) {
  if (!lane || typeof lane !== "object") return [];
  if (Array.isArray(lane.patterns)) {
    return lane.patterns
      .map((p) => (typeof p === "string" ? p.trim() : ""))
      .filter(Boolean);
  }
  if (typeof lane.pattern === "string" && lane.pattern.trim()) {
    return [lane.pattern.trim()];
  }
  return [];
}

/**
 * The "primary" pattern id for a lane. Shorthand for ``lanePatterns(lane)[0]``
 * with an explicit null for lanes that have no pattern.
 *
 * Intended for call-sites that currently only consume a single pattern
 * (shipctl run, the renderer, the dashboard). They read this and will
 * continue to work until multi-pattern execution lands (C3.2); until
 * then, a lane with ``patterns.length > 1`` is rejected upstream by
 * the executor with a clear error.
 *
 * @param {any} lane
 * @returns {string | null}
 */
export function lanePrimaryPattern(lane) {
  const list = lanePatterns(lane);
  return list.length ? list[0] : null;
}

/**
 * Resolve the effective fan-out mode for a lane.
 *
 * Returns ``LANE_FANOUT_DEFAULT`` (``matrix``) when the lane doesn't
 * declare one or has at most one pattern (in which case the concept
 * doesn't apply but we still want a deterministic value for downstream
 * consumers). Unknown / invalid values fall back to the default; the
 * validator already flags them as errors at config-load time.
 *
 * @param {any} lane
 * @returns {"matrix" | "sequential" | "concurrent"}
 */
export function laneFanout(lane) {
  if (!lane || typeof lane !== "object") return LANE_FANOUT_DEFAULT;
  if (typeof lane.fanout === "string" && LANE_FANOUT_MODES.includes(lane.fanout)) {
    return lane.fanout;
  }
  return LANE_FANOUT_DEFAULT;
}
