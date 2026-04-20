import { KNOWN_AGENTS } from "../detect.mjs";

export const CONFIG_SCHEMA_VERSION = 1;

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

export const KINDS = Object.freeze(["pattern", "tool", "workflow", "collection", "doc"]);

export const AGENT_IDS = Object.freeze(Object.keys(KNOWN_AGENTS));

export const PIN_KEY_REGEX = /^(pattern|tool|workflow|collection|doc)\/[a-zA-Z0-9_\-\.\/]+$/;

export const UUID_V4_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const SEMVER_OR_RANGE_REGEX =
  /^(\^|~|>=|<=|>|<|=)?\s*\d+(\.\d+){0,2}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/;

/**
 * Produce a fresh, independent default config (all nested objects are new).
 */
export function DEFAULT_CONFIG() {
  return {
    version: CONFIG_SCHEMA_VERSION,
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

/** @typedef {{ok:true,config:object,warnings:string[]}|{ok:false,errors:string[],warnings:string[]}} ValidationResult */

const KNOWN_TOP_LEVEL = new Set([
  "version",
  "shipctl_min",
  "api",
  "stack",
  "artifacts",
  "cache",
  "telemetry",
]);

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
 * @param {any} obj
 * @returns {ValidationResult}
 */
export function validateConfig(obj) {
  const errors = [];
  const warnings = [];

  if (!isPlainObject(obj)) {
    return { ok: false, errors: ["config must be a YAML mapping"], warnings };
  }

  if (obj.version !== CONFIG_SCHEMA_VERSION) {
    errors.push(`version: expected ${CONFIG_SCHEMA_VERSION}, got ${JSON.stringify(obj.version)}`);
  }

  pushUnknownKeyWarnings(obj, KNOWN_TOP_LEVEL, "", warnings);

  const api = obj.api;
  if (!isPlainObject(api)) {
    errors.push("api: must be an object");
  } else {
    pushUnknownKeyWarnings(api, KNOWN_API, "api", warnings);
    if (typeof api.base_url !== "string") {
      errors.push("api.base_url: must be a string URL");
    } else {
      try {
        // eslint-disable-next-line no-new
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
                `artifacts.pins[${JSON.stringify(k)}]: invalid key; expected <kind>/<id> where kind∈{pattern,tool,workflow,collection,doc}`,
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
