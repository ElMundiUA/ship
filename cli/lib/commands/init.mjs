import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import YAML from "yaml";

import {
  DEFAULT_CONFIG,
  validateConfig,
  TRACKERS,
  CIS,
  PRESETS,
  LANGUAGES,
  CHANNELS,
  AGENT_IDS,
} from "../config/schema.mjs";
import {
  writeConfig,
  writeState,
  defaultState,
  ensureAnonymousId,
  findShipRoot,
  readConfig,
  SHIP_DIR,
  CONFIG_REL,
  STATE_REL,
} from "../config/io.mjs";
import { syncArtifacts } from "./sync.mjs";
import { detectAll } from "../adapters/index.mjs";
import { listCached, readCached } from "../cache/store.mjs";
import { renderPlan, applyPlan } from "../bootstrap/render.mjs";
import { KNOWN_AGENTS } from "../detect.mjs";

const MARKER = "<!-- ship-cli: artifacts-protocol v1 -->";
const END_MARKER = "<!-- ship-cli:end artifacts-protocol -->";
const INSTALLED_FROM_RE = /<!--\s*ship-cli:\s*installed-from\s+([^\s>]+)\s*-->/g;
const FOOTER_VERSION_RE = /@(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\s*$/;

/**
 * @typedef {{
 *   cwd:string,
 *   agents:string[],
 *   tracker:string|null,
 *   ci:string|null,
 *   preset:string|null,
 *   language:string|null,
 *   channel:string|null,
 *   telemetry:"on"|"off"|"ask"|null,
 *   copyRules:boolean,
 *   copyPlaybook:boolean,
 *   bootstrap:boolean,
 *   json:boolean,
 *   yes:boolean,
 *   force:boolean,
 *   dryRun:boolean
 * }} InitOptions
 */

/**
 * @param {{baseUrl:string, yes:boolean, force:boolean, dryRun:boolean, json:boolean}} ctx
 * @param {string[]} args
 */
export async function initCommand(ctx, args) {
  if (args[0] === "help" || args[0] === "-h" || args[0] === "--help") {
    printInitHelp();
    return;
  }

  const opts = parseInitArgs(args, ctx);
  validateFlagEnums(opts);

  // ── Load existing config if present; else build a fresh one ──────────────
  const shipRootBefore = findShipRoot(opts.cwd);
  let config;
  let configFilePath;
  let configExisted = false;
  if (shipRootBefore) {
    try {
      const read = readConfig(opts.cwd);
      config = read.config;
      configFilePath = read.filePath;
      configExisted = true;
    } catch {
      config = DEFAULT_CONFIG();
      configFilePath = path.join(opts.cwd, CONFIG_REL);
    }
  } else {
    config = DEFAULT_CONFIG();
    configFilePath = path.join(opts.cwd, CONFIG_REL);
  }

  const flagSet = {
    tracker: opts.tracker !== null,
    ci: opts.ci !== null,
    preset: opts.preset !== null,
    agents: opts.agents.length > 0,
    language: opts.language !== null,
    channel: opts.channel !== null,
  };

  applyFlagOverrides(config, opts, flagSet);

  // ── Telemetry decision ───────────────────────────────────────────────────
  let telemetryMode;
  if (opts.telemetry === "on") {
    config.telemetry.share = true;
    telemetryMode = "on";
  } else if (opts.telemetry === "off") {
    config.telemetry.share = false;
    telemetryMode = "off";
  } else if (opts.telemetry === "ask" && input.isTTY && output.isTTY && !opts.dryRun) {
    telemetryMode = (await promptTelemetry()) ? "on" : "off";
    config.telemetry.share = telemetryMode === "on";
  } else if (opts.yes) {
    config.telemetry.share = false;
    telemetryMode = "off";
  } else if (!opts.dryRun && input.isTTY && output.isTTY && !configExisted) {
    telemetryMode = (await promptTelemetry()) ? "on" : "off";
    config.telemetry.share = telemetryMode === "on";
  } else {
    // Non-TTY (e.g. CI), or config already existed — keep whatever was there;
    // never auto-enable. Default `share` is false via DEFAULT_CONFIG().
    telemetryMode = config.telemetry.share === true ? "on" : "off";
  }

  ensureAnonymousId(config);

  // ── Doctor-based inference (no network) for anything left at defaults ────
  let findings = null;
  try {
    findings = await detectAll(opts.cwd);
  } catch {
    findings = null;
  }
  if (findings) {
    const proposed = proposeStack(findings);
    if (!flagSet.tracker && (config.stack.tracker == null || config.stack.tracker === "none")) {
      config.stack.tracker = proposed.tracker;
    }
    if (!flagSet.ci && (config.stack.ci == null || config.stack.ci === "manual")) {
      config.stack.ci = proposed.ci;
    }
    if (!flagSet.language && (config.stack.language == null || config.stack.language === "multi")) {
      config.stack.language = proposed.language;
    }
    if (
      !flagSet.agents &&
      (!Array.isArray(config.stack.agents) || config.stack.agents.length === 0)
    ) {
      config.stack.agents = proposed.agents;
    }
  }

  // Final validation
  const valid = validateConfig(config);
  if (!valid.ok) {
    for (const w of valid.warnings) process.stderr.write(`warn: ${w}\n`);
    for (const e of valid.errors) process.stderr.write(`${e}\n`);
    process.exit(10);
  }
  for (const w of valid.warnings) process.stderr.write(`warn: ${w}\n`);

  // ── Derived artifact list to fetch via syncArtifacts ─────────────────────
  const derived = buildDerivedList(config, opts);

  // ── Dry-run short-circuit: emit plan only, write nothing ─────────────────
  if (opts.dryRun) {
    const plan = buildPlanSummary(opts.cwd, config, opts, telemetryMode, derived);
    if (opts.json) {
      process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
    } else {
      printHumanPlan(plan, opts);
    }
    process.stdout.write("(dry-run: no files written)\n");
    return;
  }

  // ── Write config / state / cache dir / .gitignore ────────────────────────
  const ensured = ensureShipLayout(opts.cwd, config, configFilePath, configExisted);
  const shipRoot = findShipRoot(opts.cwd);
  if (!shipRoot) {
    throw new Error("init: failed to locate .ship/ after creation");
  }

  // ── Sync relevant artifacts into .ship/cache/ ────────────────────────────
  let syncSummary = null;
  if (derived.length) {
    try {
      syncSummary = await syncArtifacts({
        cwd: shipRoot,
        baseUrl: ctx.baseUrl,
        channel: opts.channel || config.api?.channel,
        onlyKinds: ["collection"],
        include: derived,
        verbose: false,
      });
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      process.stderr.write(`warn: artifact fetch partially failed (${msg})\n`);
    }
  }

  // ── --copy-rules: materialize agent rules from cache onto disk ───────────
  const ruleInstallations = [];
  if (opts.copyRules) {
    for (const agent of config.stack.agents || []) {
      const res = installAgentRule(shipRoot, agent, { force: opts.force });
      if (res) ruleInstallations.push(res);
    }
  }

  // ── --bootstrap: render CI/tracker scaffolding ───────────────────────────
  let bootstrapSummary = null;
  if (opts.bootstrap) {
    const presetArtifact = readPresetArtifact(shipRoot, config);
    const plan = renderPlan(config, presetArtifact);
    const results = applyPlan(shipRoot, plan, { dryRun: false, force: opts.force });
    bootstrapSummary = { files: plan.summary.files, notes: plan.summary.notes, results };
  }

  // ── --copy-playbook: copy cached playbook to .ship/playbooks/ ────────────
  let playbookCopied = null;
  if (opts.copyPlaybook) {
    playbookCopied = copyPlaybookFromCache(shipRoot);
  }

  // ── Output ───────────────────────────────────────────────────────────────
  const summary = {
    ok: true,
    cwd: opts.cwd,
    ship_root: shipRoot,
    config_path: ensured.configFilePath,
    telemetry: telemetryMode,
    stack: {
      tracker: config.stack.tracker,
      ci: config.stack.ci,
      preset: config.stack.preset,
      language: config.stack.language,
      agents: [...(config.stack.agents || [])],
    },
    channel: config.api?.channel || "stable",
    rules: ruleInstallations,
    bootstrap: bootstrapSummary,
    playbook: playbookCopied,
    sync: syncSummary
      ? {
          up_to_date: syncSummary.up_to_date,
          updated: syncSummary.updated,
          failed: syncSummary.failed,
          entries: syncSummary.entries,
        }
      : null,
  };

  if (opts.json) {
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
    return;
  }

  printHumanSummary(summary, ensured, opts);
}

// ── Arg parsing ────────────────────────────────────────────────────────────
/**
 * @param {string[]} args
 * @param {{yes:boolean,force:boolean,dryRun:boolean,json:boolean}} ctx
 * @returns {InitOptions}
 */
function parseInitArgs(args, ctx) {
  /** @type {InitOptions} */
  const opts = {
    cwd: process.cwd(),
    agents: [],
    tracker: null,
    ci: null,
    preset: null,
    language: null,
    channel: null,
    telemetry: null,
    copyRules: false,
    copyPlaybook: false,
    bootstrap: false,
    json: !!ctx.json,
    yes: !!ctx.yes,
    force: !!ctx.force,
    dryRun: !!ctx.dryRun,
  };

  const agentsCsv = [];
  let onlyAgent = null;

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--cwd" && args[i + 1]) {
      opts.cwd = path.resolve(String(args[++i]));
      continue;
    }
    if (a.startsWith("--cwd=")) {
      opts.cwd = path.resolve(a.slice("--cwd=".length));
      continue;
    }
    if (a === "--yes" || a === "-y") {
      opts.yes = true;
      continue;
    }
    if (a === "--force") {
      opts.force = true;
      continue;
    }
    if (a === "--dry-run") {
      opts.dryRun = true;
      continue;
    }
    if (a === "--json") {
      opts.json = true;
      continue;
    }
    if (a === "--agents" && args[i + 1]) {
      for (const s of String(args[++i]).split(",")) {
        const id = s.trim();
        if (id) agentsCsv.push(id);
      }
      continue;
    }
    if (a === "--only" && args[i + 1]) {
      onlyAgent = String(args[++i]).trim();
      continue;
    }
    if (a === "--tracker" && args[i + 1]) {
      opts.tracker = String(args[++i]);
      continue;
    }
    if (a === "--ci" && args[i + 1]) {
      opts.ci = String(args[++i]);
      continue;
    }
    if (a === "--preset" && args[i + 1]) {
      opts.preset = String(args[++i]);
      continue;
    }
    if (a === "--language" && args[i + 1]) {
      opts.language = String(args[++i]);
      continue;
    }
    if (a === "--channel" && args[i + 1]) {
      opts.channel = String(args[++i]);
      continue;
    }
    if (a === "--telemetry" && args[i + 1]) {
      const v = String(args[++i]).trim().toLowerCase();
      if (v !== "on" && v !== "off" && v !== "ask") {
        process.stderr.write(`init: --telemetry must be on|off|ask (got "${v}")\n`);
        process.exit(1);
      }
      opts.telemetry = /** @type {"on"|"off"|"ask"} */ (v);
      continue;
    }
    if (a === "--copy-rules") {
      opts.copyRules = true;
      continue;
    }
    if (a === "--copy-playbook") {
      opts.copyPlaybook = true;
      continue;
    }
    if (a === "--bootstrap") {
      opts.bootstrap = true;
      continue;
    }
  }

  opts.agents = agentsCsv.length ? agentsCsv : onlyAgent ? [onlyAgent] : [];
  return opts;
}

/** @param {InitOptions} opts */
function validateFlagEnums(opts) {
  if (opts.tracker && !TRACKERS.includes(opts.tracker)) {
    process.stderr.write(`init: unknown --tracker "${opts.tracker}". Allowed: ${TRACKERS.join(", ")}\n`);
    process.exit(1);
  }
  if (opts.ci && !CIS.includes(opts.ci)) {
    process.stderr.write(`init: unknown --ci "${opts.ci}". Allowed: ${CIS.join(", ")}\n`);
    process.exit(1);
  }
  if (opts.preset && !PRESETS.includes(opts.preset)) {
    process.stderr.write(`init: unknown --preset "${opts.preset}". Allowed: ${PRESETS.join(", ")}\n`);
    process.exit(1);
  }
  if (opts.language && !LANGUAGES.includes(opts.language)) {
    process.stderr.write(`init: unknown --language "${opts.language}". Allowed: ${LANGUAGES.join(", ")}\n`);
    process.exit(1);
  }
  if (opts.channel && !CHANNELS.includes(opts.channel)) {
    process.stderr.write(`init: unknown --channel "${opts.channel}". Allowed: ${CHANNELS.join(", ")}\n`);
    process.exit(1);
  }
  for (const a of opts.agents) {
    if (!AGENT_IDS.includes(a)) {
      process.stderr.write(
        `init: unknown agent "${a}". Allowed: ${AGENT_IDS.slice().sort().join(", ")}\n`,
      );
      process.exit(1);
    }
  }
}

/** @param {object} config @param {InitOptions} opts @param {Record<string,boolean>} flagSet */
function applyFlagOverrides(config, opts, flagSet) {
  if (!config.stack || typeof config.stack !== "object") config.stack = {};
  if (!config.api || typeof config.api !== "object") config.api = {};
  if (!config.telemetry || typeof config.telemetry !== "object") config.telemetry = {};

  if (flagSet.tracker) config.stack.tracker = opts.tracker;
  if (flagSet.ci) config.stack.ci = opts.ci;
  if (flagSet.preset) config.stack.preset = opts.preset;
  if (flagSet.language) config.stack.language = opts.language;
  if (flagSet.agents) config.stack.agents = [...opts.agents];
  if (flagSet.channel) config.api.channel = opts.channel;
}

// ── Doctor / stack inference ───────────────────────────────────────────────
function proposeStack(findings) {
  const pickTop = (arr, { min = 0.1, exclude = [], fallback = null } = {}) => {
    const pool = arr.filter(
      (e) => e.present && e.confidence > min && !exclude.includes(e.id),
    );
    return pool.length ? pool[0].id : fallback;
  };
  const tracker = pickTop(findings.trackers, { exclude: ["none"], fallback: "none" });
  const ci = pickTop(findings.ci, { exclude: ["manual"], fallback: "manual" });
  const language = pickTop(findings.language, { fallback: "multi" }) || "multi";
  const agents = (findings.agents || [])
    .filter((a) => a.present && a.confidence >= 0.5)
    .map((a) => a.id)
    .filter((id) => AGENT_IDS.includes(id));
  return { tracker, ci, language, agents };
}

// ── Derived artifact list ──────────────────────────────────────────────────
/**
 * @param {object} config
 * @param {InitOptions} opts
 * @returns {Array<{kind:string,id:string}>}
 */
function buildDerivedList(config, opts) {
  const list = [];
  const seen = new Set();
  const add = (kind, id) => {
    const key = `${kind}:${id}`;
    if (seen.has(key)) return;
    seen.add(key);
    list.push({ kind, id });
  };
  for (const a of config.stack.agents || []) {
    add("collection", `agent-rules-${a}`);
  }
  if (config.stack.preset) {
    add("collection", `preset-${config.stack.preset}`);
  }
  if (opts.copyPlaybook) {
    add("collection", "adoption-playbook");
  }
  return list;
}

// ── Ship layout creation ───────────────────────────────────────────────────
function ensureShipLayout(cwd, config, configFilePath, configExisted) {
  const shipDir = path.join(cwd, SHIP_DIR);
  fs.mkdirSync(shipDir, { recursive: true });

  let configWritten = false;
  if (!configExisted) {
    writeConfig(configFilePath, config);
    configWritten = true;
  } else {
    // Persist flag-driven updates back to disk when config already existed.
    writeConfig(configFilePath, config);
    configWritten = true;
  }

  const statePath = path.join(cwd, STATE_REL);
  if (!fs.existsSync(statePath)) {
    writeState(cwd, defaultState());
  }

  const cacheDir = path.join(shipDir, "cache");
  fs.mkdirSync(cacheDir, { recursive: true });
  const keep = path.join(cacheDir, ".gitkeep");
  if (!fs.existsSync(keep)) fs.writeFileSync(keep, "", "utf8");

  const giResult = ensureGitignore(cwd);

  return {
    configFilePath,
    configWritten,
    configExisted,
    gitignorePath: giResult.path,
    gitignoreChanged: giResult.changed,
  };
}

function ensureGitignore(cwd) {
  const giPath = path.join(cwd, ".gitignore");
  const entries = [
    "# Ship",
    ".ship/cache/",
    ".ship/telemetry-outbox.jsonl",
    ".ship/feedback-drafts/",
    ".ship/state.json",
  ];
  let current = "";
  if (fs.existsSync(giPath)) current = fs.readFileSync(giPath, "utf8");
  const existingLines = new Set(current.split(/\r?\n/).map((l) => l.trim()));
  const toAppend = entries.filter((e) => !existingLines.has(e.trim()));
  if (toAppend.length === 0) return { path: giPath, changed: false };
  const prefix = current.length === 0 || current.endsWith("\n") ? "" : "\n";
  const tail =
    current.length === 0 ? `${toAppend.join("\n")}\n` : `${prefix}${toAppend.join("\n")}\n`;
  fs.writeFileSync(giPath, current + tail, "utf8");
  return { path: giPath, changed: true };
}

// ── Agent rule installation ────────────────────────────────────────────────
/**
 * @param {string} shipRoot
 * @param {string} agent
 * @param {{force:boolean}} opts
 * @returns {null | {agent:string, path:string, action:string, from:string}}
 */
function installAgentRule(shipRoot, agent, { force }) {
  const id = `agent-rules-${agent}`;
  const version = latestCachedVersion(shipRoot, "collection", id);
  if (!version) {
    process.stderr.write(
      `warn: --copy-rules: no cached artifact for collection/${id} (was the fetch successful?)\n`,
    );
    return null;
  }
  const cached = readCached(shipRoot, "collection", id, version);
  if (!cached) return null;
  const parsed = parseFrontmatter(cached.content);
  const target = parsed.attrs.install_target || fallbackInstallTarget(agent);
  if (!target) {
    process.stderr.write(`warn: --copy-rules: no install_target for ${agent}; skipping\n`);
    return null;
  }
  const marker = parsed.attrs.marker || MARKER;
  const body = parsed.body.trimEnd();
  const footer = `<!-- ship-cli: installed-from collection/${id}@${version} -->`;

  const absTarget = path.isAbsolute(target) ? target : path.join(shipRoot, target);
  const existed = fs.existsSync(absTarget);
  const prev = existed ? fs.readFileSync(absTarget, "utf8") : "";

  // If installed-from references a different version and --force is not set, skip.
  if (existed) {
    const existingVersion = extractInstalledVersion(prev);
    if (existingVersion && existingVersion !== version && !force) {
      process.stderr.write(
        `warn: ${path.relative(shipRoot, absTarget)} has ship-cli installed-from @${existingVersion}; pass --force to replace with @${version}\n`,
      );
      return {
        agent,
        path: path.relative(shipRoot, absTarget) || absTarget,
        action: "skipped",
        from: `collection/${id}@${version}`,
      };
    }
  }

  const next = upsertMarkedBlock(prev, { marker, endMarker: END_MARKER, body, footer });

  fs.mkdirSync(path.dirname(absTarget), { recursive: true });
  fs.writeFileSync(absTarget, next, "utf8");

  return {
    agent,
    path: path.relative(shipRoot, absTarget) || absTarget,
    action: existed ? "updated" : "wrote",
    from: `collection/${id}@${version}`,
  };
}

function fallbackInstallTarget(agent) {
  const meta = KNOWN_AGENTS[agent];
  if (!meta) return null;
  return path.join(...meta.targetRel);
}

/**
 * Parse a YAML front-matter block if present.
 * @param {string} text
 * @returns {{attrs:object, body:string}}
 */
function parseFrontmatter(text) {
  if (!text.startsWith("---\n") && !text.startsWith("---\r\n")) {
    return { attrs: {}, body: text };
  }
  const rest = text.slice(4);
  const endIdx = rest.indexOf("\n---\n");
  const altEndIdx = rest.indexOf("\n---\r\n");
  const end = endIdx >= 0 ? endIdx : altEndIdx >= 0 ? altEndIdx : -1;
  if (end < 0) return { attrs: {}, body: text };
  const fmText = rest.slice(0, end);
  const body = rest.slice(end + (endIdx >= 0 ? 5 : 6));
  let attrs = {};
  try {
    const parsed = YAML.parse(fmText);
    if (parsed && typeof parsed === "object") attrs = parsed;
  } catch {
    attrs = {};
  }
  return { attrs, body };
}

/**
 * Idempotent upsert of a marker-delimited block + footer line.
 *   prev  : current file text (may be empty)
 *   marker, endMarker : <!-- … --> tokens
 *   body  : replacement body (should contain or wrap with the markers itself;
 *           we just splice it in verbatim)
 *   footer : single line `<!-- ship-cli: installed-from … -->`
 */
function upsertMarkedBlock(prev, { marker, endMarker, body, footer }) {
  // Strip every existing "installed-from" footer so we never duplicate them.
  let stripped = prev.replace(INSTALLED_FROM_RE, "");
  // Collapse any 3+ consecutive newlines left by the strip.
  stripped = stripped.replace(/\n{3,}/g, "\n\n");

  let out;
  if (stripped.includes(marker) && stripped.includes(endMarker)) {
    const start = stripped.indexOf(marker);
    const endAt = stripped.indexOf(endMarker, start) + endMarker.length;
    const before = stripped.slice(0, start).replace(/\s+$/, "");
    const after = stripped.slice(endAt).replace(/^\s+/, "");
    out = `${before}${before ? "\n\n" : ""}${body.trim()}\n${after ? `\n${after}` : ""}`;
  } else if (stripped.trim().length === 0) {
    out = `${body.trim()}\n`;
  } else {
    out = `${stripped.replace(/\s+$/, "")}\n\n${body.trim()}\n`;
  }

  // Append a single footer line.
  out = `${out.replace(/\s+$/, "")}\n\n${footer}\n`;
  return out;
}

function extractInstalledVersion(text) {
  const matches = [...text.matchAll(INSTALLED_FROM_RE)];
  if (!matches.length) return null;
  const last = matches[matches.length - 1][1];
  const m = last.match(FOOTER_VERSION_RE);
  return m ? m[1] : null;
}

// ── Cache helpers ──────────────────────────────────────────────────────────
function latestCachedVersion(shipRoot, kind, id) {
  const all = listCached(shipRoot).filter((c) => c.kind === kind && c.id === id);
  if (!all.length) return null;
  all.sort((a, b) => cmpSemver(b.version, a.version));
  return all[0].version;
}

function cmpSemver(a, b) {
  const pa = String(a).split(/[.-]/).map((x) => (Number.isNaN(Number(x)) ? x : Number(x)));
  const pb = String(b).split(/[.-]/).map((x) => (Number.isNaN(Number(x)) ? x : Number(x)));
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const xa = pa[i];
    const xb = pb[i];
    if (xa === undefined) return -1;
    if (xb === undefined) return 1;
    if (xa === xb) continue;
    if (typeof xa === typeof xb) return xa < xb ? -1 : 1;
    return typeof xa === "number" ? -1 : 1;
  }
  return 0;
}

function readPresetArtifact(shipRoot, config) {
  const preset = config.stack?.preset;
  if (!preset) return null;
  const id = `preset-${preset}`;
  const version = latestCachedVersion(shipRoot, "collection", id);
  if (!version) return null;
  const cached = readCached(shipRoot, "collection", id, version);
  if (!cached) return null;
  const { attrs, body } = parseFrontmatter(cached.content);
  return { id, version, attrs, body };
}

function copyPlaybookFromCache(shipRoot) {
  const version = latestCachedVersion(shipRoot, "collection", "adoption-playbook");
  if (!version) return null;
  const cached = readCached(shipRoot, "collection", "adoption-playbook", version);
  if (!cached) return null;
  const dest = path.join(shipRoot, ".ship", "playbooks", `adoption-playbook@${version}.md`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, cached.content, "utf8");
  return { path: path.relative(shipRoot, dest), version };
}

// ── Telemetry prompt ───────────────────────────────────────────────────────
async function promptTelemetry() {
  const rl = readline.createInterface({ input, output });
  try {
    const ans = (
      await rl.question(
        "Share anonymous artifact usage with Ship to improve the methodology? [y/N] ",
      )
    )
      .trim()
      .toLowerCase();
    return ans === "y" || ans === "yes";
  } finally {
    rl.close();
  }
}

// ── Plan summary (dry-run) ─────────────────────────────────────────────────
function buildPlanSummary(cwd, config, opts, telemetryMode, derived) {
  const stack = {
    tracker: config.stack.tracker,
    ci: config.stack.ci,
    preset: config.stack.preset,
    language: config.stack.language,
    agents: [...(config.stack.agents || [])],
  };
  const rules = opts.copyRules
    ? (config.stack.agents || []).map((a) => ({
        agent: a,
        path:
          (KNOWN_AGENTS[a] && KNOWN_AGENTS[a].targetRel.join("/")) ||
          `.ship/rules/${a}.md`,
        from: `collection/agent-rules-${a}@<latest>`,
      }))
    : [];
  const bootstrapPreview = opts.bootstrap
    ? renderPlan(config, null).summary
    : null;
  return {
    ok: true,
    dry_run: true,
    cwd,
    config_path: path.join(cwd, CONFIG_REL),
    telemetry: telemetryMode,
    channel: config.api?.channel || "stable",
    stack,
    artifacts_to_fetch: derived,
    rules,
    bootstrap: bootstrapPreview,
    playbook: opts.copyPlaybook ? { requested: true, fetched: false } : null,
  };
}

function printHumanPlan(plan, opts) {
  const lines = [];
  lines.push("Ship init — planned changes");
  lines.push("---------------------------");
  lines.push(`cwd:       ${plan.cwd}`);
  lines.push(`config:    ${plan.config_path}`);
  lines.push(`telemetry: ${plan.telemetry}`);
  lines.push(`channel:   ${plan.channel}`);
  lines.push(
    `stack:     tracker=${plan.stack.tracker} ci=${plan.stack.ci} preset=${plan.stack.preset} language=${plan.stack.language}`,
  );
  lines.push(`agents:    ${plan.stack.agents.join(", ") || "(none)"}`);
  if (plan.artifacts_to_fetch.length) {
    lines.push("");
    lines.push("Artifacts to fetch:");
    for (const a of plan.artifacts_to_fetch) lines.push(`  - ${a.kind}/${a.id}`);
  }
  if (plan.rules.length) {
    lines.push("");
    lines.push("Rules to install (--copy-rules):");
    for (const r of plan.rules) lines.push(`  - ${r.path}  (${r.from})`);
  }
  if (plan.bootstrap) {
    lines.push("");
    lines.push("Bootstrap plan:");
    for (const f of plan.bootstrap.files) lines.push(`  - ${f.mode}: ${f.path}`);
  }
  if (plan.playbook) {
    lines.push("");
    lines.push("--copy-playbook: requested (fetched during real run only)");
  }
  if (!opts.copyRules) {
    lines.push("");
    lines.push("(--copy-rules not set: rules files will NOT be installed)");
  }
  process.stdout.write(`${lines.join("\n")}\n\n`);
}

// ── Human summary (real run) ───────────────────────────────────────────────
function printHumanSummary(summary, ensured, opts) {
  const lines = [];
  lines.push("Ship init complete");
  lines.push("-----------------");
  lines.push(`Config:    ${path.relative(summary.cwd, summary.config_path) || summary.config_path}`);
  lines.push(
    `Agents:    ${summary.stack.agents.length ? summary.stack.agents.join(", ") : "(none)"}`,
  );
  lines.push(`Tracker:   ${summary.stack.tracker}`);
  lines.push(`CI:        ${summary.stack.ci}`);
  lines.push(`Preset:    ${summary.stack.preset}`);
  lines.push(`Channel:   ${summary.channel}`);
  lines.push(`Telemetry: ${summary.telemetry}`);

  if (summary.rules.length) {
    lines.push("");
    lines.push("Installed rules:");
    for (const r of summary.rules) {
      lines.push(`  - ${r.action} ${r.path} (from ${r.from})`);
    }
  }

  if (summary.bootstrap) {
    lines.push("");
    lines.push(`Bootstrap (preset=${summary.stack.preset}):`);
    for (const r of summary.bootstrap.results) {
      lines.push(`  - ${r.action}: ${r.path}`);
    }
  }

  if (summary.playbook) {
    lines.push("");
    lines.push(`Playbook: wrote ${summary.playbook.path} (@${summary.playbook.version})`);
  } else if (opts.copyPlaybook) {
    lines.push("");
    lines.push("Playbook: not found on manifest (skipped)");
  }

  if (summary.sync) {
    lines.push("");
    lines.push(
      `Sync: up_to_date=${summary.sync.up_to_date} updated=${summary.sync.updated} failed=${summary.sync.failed}`,
    );
  }

  lines.push("");
  lines.push("Next:");
  lines.push("  shipctl sync              # keep artifacts fresh");
  lines.push("  shipctl verify            # check tracker labels, CI secrets, rules markers");
  lines.push("  shipctl feedback draft    # submit improvement idea");

  process.stdout.write(`${lines.join("\n")}\n`);
}

// ── Help ──────────────────────────────────────────────────────────────────-
function printInitHelp() {
  const agentsList = AGENT_IDS.slice().sort().join(", ");
  process.stdout.write(`shipctl init — bootstrap .ship/, fetch artifacts, install agent rules.

USAGE
  shipctl init [--yes] [--force] [--dry-run] [--cwd DIR] [--json]
               [--agents cursor,codex,claude-md] [--only <id>]
               [--tracker <name>] [--ci <name>] [--preset <name>]
               [--language <id>] [--channel stable|edge]
               [--copy-rules] [--copy-playbook] [--bootstrap]
               [--telemetry on|off|ask]

FLAGS
  --yes              Non-interactive: skip confirmation prompts.
  --force            Replace existing ship-managed blocks with current content.
  --dry-run          Preview only; no files written, no network writes.
  --json             Emit the final summary as a JSON object (stdout).
  --cwd DIR          Operate against DIR instead of the current working dir.
  --agents <csv>     Comma-separated agent ids (overrides --only). Example: cursor,codex,claude-md.
  --only <id>        Single agent id (back-compat with older init).
  --tracker <name>   Stack tracker: ${TRACKERS.join("|")}
  --ci <name>        Stack CI: ${CIS.join("|")}
  --preset <name>    Stack preset: ${PRESETS.join("|")}
  --language <id>    Repo language: ${LANGUAGES.join("|")}
  --channel <c>      Override config.api.channel: ${CHANNELS.join("|")}
  --copy-rules       Install collection/agent-rules-<agent>@<v> from cache to its install_target.
  --copy-playbook    Try to fetch collection/adoption-playbook and copy it under .ship/playbooks/.
  --bootstrap        Also render CI/tracker scaffolding (SHIP_BOOTSTRAP_PLAN.md etc.).
  --telemetry        Explicit telemetry choice (default: prompt on first init, off in --yes / non-TTY).

BEHAVIOR
  1. Ensures .ship/ exists and writes config.yml + state.json + cache/.gitkeep + .gitignore.
  2. Runs built-in adapter detection (doctor --no-network) to propose any stack fields
     the flags / existing config left at defaults.
  3. Calls shipctl sync for collection/agent-rules-<agent> + collection/preset-<preset>.
  4. With --copy-rules, installs each cached rules artifact to its install_target,
     preserving unrelated content and marker-guarded sections. Re-runs are idempotent;
     --force replaces a previously-installed different version.
  5. With --bootstrap, renders CI/tracker skeletons from the preset artifact
     (full support: mobile-app + gh-actions + linear; plan-only otherwise).

KNOWN AGENT IDS
  ${agentsList}

EXAMPLES
  shipctl init --yes --agents cursor,claude-md --copy-rules --telemetry off
  shipctl init --yes --bootstrap --agents cursor,codex --tracker linear \\
               --ci gh-actions --preset mobile-app --copy-rules
  shipctl init --dry-run --agents cursor --copy-rules --bootstrap \\
               --preset mobile-app --ci gh-actions --tracker linear
`);
}
