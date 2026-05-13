import fs from "node:fs";
import path from "node:path";
import { detectAll } from "../adapters/index.mjs";
import { isFile, isDir, pkgDeps, readJson } from "../adapters/_fs.mjs";
import { findShipRoot, readConfig } from "../config/io.mjs";
import { resolveAgentSignal } from "../detect.mjs";

const INVENTORY_REL = path.join(".ship", "inventory.json");
const CONFIG_REL = path.join(".ship", "config.yml");
const CACHE_REL = path.join(".ship", "cache");

/**
 * @param {{ json: boolean, yes: boolean, force: boolean, dryRun: boolean }} ctx
 * @param {string[]} args
 */
export async function doctorCommand(ctx, args) {
  if (args[0] === "help" || args[0] === "-h" || args[0] === "--help") {
    printDoctorHelp();
    return;
  }

  let cwd = process.cwd();
  let writeInventory = false;
  let jsonOut = !!ctx.json;
  /* eslint-disable-next-line no-unused-vars */
  let noNetwork = false;

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--cwd" && args[i + 1]) {
      cwd = path.resolve(String(args[++i]));
      continue;
    }
    if (a.startsWith("--cwd=")) {
      cwd = path.resolve(a.slice("--cwd=".length));
      continue;
    }
    if (a === "--write-inventory") {
      writeInventory = true;
      continue;
    }
    if (a === "--json") {
      jsonOut = true;
      continue;
    }
    if (a === "--no-network") {
      noNetwork = true;
      continue;
    }
    if (a === "--help" || a === "-h") {
      printDoctorHelp();
      return;
    }
    throw new Error(`doctor: unknown argument: ${a}`);
  }

  const findings = await detectAll(cwd);
  const inferred = inferStack(cwd, findings);
  const presetInfo = inferPreset(cwd);
  inferred.preset = presetInfo.preset;

  const existing = shipArtifactsSnapshot(cwd);

  const configInfo = loadShipConfig(cwd);
  const reconciled = reconcileStack(findings, inferred, configInfo);

  const report = {
    version: 1,
    detected_at: new Date().toISOString(),
    cwd: path.resolve(cwd),
    findings,
    inferred,
    preset_evidence: presetInfo.evidence,
    existing,
    config: configInfo.stack,
    disk: { ...inferred, preset_evidence: presetInfo.evidence },
    reconciled,
    recommendations: buildRecommendations({
      inferred,
      existing,
      config: configInfo.stack,
      reconciled,
    }),
  };

  if (jsonOut) {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  } else {
    printHumanReport(report);
  }

  if (writeInventory) {
    const invPath = await writeInventoryFile(cwd, report);
    if (!jsonOut) {
      process.stdout.write(`\nWrote ${path.relative(cwd, invPath) || invPath}.\n`);
    }
  }
}

function printDoctorHelp() {
  console.log(`shipctl doctor — inspect a repository and propose a Ship stack.

USAGE
  shipctl doctor [--json] [--cwd DIR] [--write-inventory] [--no-network]

DESCRIPTION
  Runs every registered tracker/CI/language/agent adapter's detect() hook
  against the target repo, prints a human-readable report (or JSON with
  --json), and optionally writes .ship/inventory.json for consumption by
  'shipctl init --bootstrap'.

FLAGS
  --cwd DIR           Inspect DIR instead of the current working directory.
  --write-inventory   Persist findings to .ship/inventory.json.
  --json              Machine-readable JSON output.
  --no-network        Reserved; doctor never makes network calls in v1.
`);
}

/**
 * Produce the inferred stack fields. Picks the highest-confidence non-zero
 * adapter per category, falling back to `none`/`manual` for tracker/ci when
 * nothing confident was detected, and to `multi` for language.
 */
function inferStack(_cwd, findings) {
  const pickTop = (arr, { min = 0, fallback = null } = {}) => {
    const confident = arr.filter((e) => e.present && e.confidence > min);
    if (confident.length) return confident[0].id;
    return fallback;
  };

  const trackerExclFallback = findings.trackers.filter((t) => t.id !== "none");
  const ciExclFallback = findings.ci.filter((c) => c.id !== "manual");

  const tracker = pickTop(trackerExclFallback, { min: 0.1, fallback: "none" });
  const ci = pickTop(ciExclFallback, { min: 0.1, fallback: "manual" });
  const language = pickTop(findings.language, { min: 0.1, fallback: "multi" }) || "multi";

  const agents = findings.agents
    .filter((a) => a.present && a.confidence >= 0.5)
    .map((a) => a.id);

  return {
    tracker,
    ci,
    language,
    agents,
    preset: "adoption-minimum",
  };
}

/**
 * Inspect the repo for preset heuristics. Returns the first match per the
 * priority in the RFC-matching task spec; `adoption-minimum` if none match.
 */
function inferPreset(cwd) {
  const evidence = [];

  const pkg = readJson(cwd, "package.json");
  const deps = pkgDeps(pkg);
  const hasDep = (name) => Object.prototype.hasOwnProperty.call(deps, name);

  // Mobile app
  if (isFile(cwd, "pubspec.yaml")) {
    evidence.push("pubspec.yaml (Flutter / Dart)");
    return { preset: "mobile-app", evidence };
  }
  if (isDir(cwd, "ios") && isDir(cwd, "android")) {
    evidence.push("ios/ and android/ directories");
    return { preset: "mobile-app", evidence };
  }
  if (hasDep("react-native")) {
    evidence.push("react-native in deps");
    return { preset: "mobile-app", evidence };
  }
  if (hasDep("expo")) {
    evidence.push("expo in deps");
    return { preset: "mobile-app", evidence };
  }

  // Monorepo
  if (isDir(cwd, "packages")) {
    evidence.push("packages/ directory");
    return { preset: "monorepo", evidence };
  }
  if (isFile(cwd, "pnpm-workspace.yaml")) {
    evidence.push("pnpm-workspace.yaml");
    return { preset: "monorepo", evidence };
  }
  if (isFile(cwd, "lerna.json")) {
    evidence.push("lerna.json");
    return { preset: "monorepo", evidence };
  }
  if (isFile(cwd, "turbo.json")) {
    evidence.push("turbo.json");
    return { preset: "monorepo", evidence };
  }

  // Web app
  for (const f of ["next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"]) {
    if (isFile(cwd, f)) {
      evidence.push(f);
      return { preset: "web-app", evidence };
    }
  }
  for (const f of ["vite.config.js", "vite.config.mjs", "vite.config.ts", "vite.config.cjs"]) {
    if (isFile(cwd, f)) {
      evidence.push(f);
      return { preset: "web-app", evidence };
    }
  }
  for (const f of ["svelte.config.js", "svelte.config.mjs", "svelte.config.ts"]) {
    if (isFile(cwd, f)) {
      evidence.push(f);
      return { preset: "web-app", evidence };
    }
  }

  // API backend
  if (isFile(cwd, "Dockerfile")) {
    const hasBackendEntry =
      isFile(cwd, "main.py") ||
      isFile(cwd, "server.ts") ||
      isFile(cwd, "server.js") ||
      isFile(cwd, "app.py") ||
      isFile(cwd, "app.ts");
    const hasUiHint =
      isFile(cwd, "index.html") ||
      isDir(cwd, "public") ||
      isDir(cwd, "src/pages") ||
      isDir(cwd, "app");
    if (hasBackendEntry && !hasUiHint) {
      evidence.push("Dockerfile + backend entry (main.py|server.ts) and no UI folder");
      return { preset: "api-backend", evidence };
    }
  }

  // CLI
  if (isDir(cwd, "bin") && pkg && typeof pkg.bin === "object") {
    evidence.push("bin/ + package.json:bin");
    return { preset: "cli", evidence };
  }
  if (isFile(cwd, "go.mod") && isDir(cwd, "cmd")) {
    evidence.push("go.mod + cmd/");
    return { preset: "cli", evidence };
  }
  if (isFile(cwd, "Cargo.toml") && isDir(cwd, "src/bin")) {
    evidence.push("Cargo.toml + src/bin/");
    return { preset: "cli", evidence };
  }

  return { preset: "adoption-minimum", evidence: ["no strong preset signals"] };
}

function shipArtifactsSnapshot(cwd) {
  const cursorRulesHit = fs.existsSync(path.join(cwd, ".cursor", "rules"))
    ? detectCursorShipRules(cwd)
    : null;
  return {
    config_yml: isFile(cwd, CONFIG_REL) ? "present" : "missing",
    cache_dir: isDir(cwd, CACHE_REL) ? "present" : "missing",
    inventory_json: isFile(cwd, INVENTORY_REL) ? "present" : "missing",
    cursor_ship_rules: cursorRulesHit || "missing",
  };
}

function detectCursorShipRules(cwd) {
  const dir = path.join(cwd, ".cursor", "rules");
  let entries;
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return "missing";
  }
  const hit = entries.find((n) => n.startsWith("ship-"));
  return hit ? `present (${hit})` : "missing";
}

function recommendations(inferred, existing) {
  const steps = [];
  if (existing.config_yml !== "present") {
    steps.push("shipctl config init");
  }
  const agentsPart = inferred.agents.length ? ` --agents ${inferred.agents.join(",")}` : "";
  steps.push(
    `shipctl init --bootstrap --tracker ${inferred.tracker} --ci ${inferred.ci}${agentsPart} --preset ${inferred.preset}`,
  );
  steps.push("shipctl sync");
  steps.push("shipctl verify");
  return steps;
}

/**
 * Load .ship/config.yml starting from `cwd` (walking upward). Returns the
 * declared stack subtree (tracker/ci/language/preset/agents plus
 * api.channel) or null if no config is present. Parse errors are swallowed
 * into `null` so doctor never crashes on malformed YAML — the disk-only
 * recommendation path still runs.
 *
 * @param {string} cwd
 * @returns {{filePath:string|null, stack: null | {tracker:string|null, ci:string|null, language:string|null, preset:string|null, agents:string[], channel:string|null}}}
 */
function loadShipConfig(cwd) {
  try {
    const root = findShipRoot(cwd);
    if (!root) return { filePath: null, stack: null };
    const { config, filePath } = readConfig(root);
    const s = (config && config.stack) || {};
    const api = (config && config.api) || {};
    return {
      filePath,
      stack: {
        tracker: typeof s.tracker === "string" ? s.tracker : null,
        ci: typeof s.ci === "string" ? s.ci : null,
        language: typeof s.language === "string" ? s.language : null,
        preset: typeof s.preset === "string" ? s.preset : null,
        agents: Array.isArray(s.agents) ? [...s.agents] : [],
        channel: typeof api.channel === "string" ? api.channel : null,
      },
    };
  } catch {
    return { filePath: null, stack: null };
  }
}

/**
 * Merge disk-inferred signals with the declared `.ship/config.yml` stack.
 * For tracker/ci/language/preset, config wins when present. For agents,
 * we union config-declared ids with disk-detected ids (after mapping raw
 * signals like `agents-md` → `codex` via `resolveAgentSignal`).
 *
 * Returns both the merged view and per-agent provenance so the JSON /
 * human reporter can explain why AGENTS.md was counted as codex.
 *
 * @param {{trackers:Array, ci:Array, language:Array, agents:Array}} findings
 * @param {{tracker:string, ci:string, language:string, agents:string[], preset:string}} inferred
 * @param {ReturnType<typeof loadShipConfig>} configInfo
 */
function reconcileStack(findings, inferred, configInfo) {
  const config = configInfo.stack;
  const configAgents = config ? config.agents : [];

  const diskAgentSignals = (findings.agents || [])
    .filter((a) => a.present && a.confidence >= 0.5)
    .map((a) => ({
      signal: a.id,
      resolved: resolveAgentSignal(a.id, configAgents),
      confidence: a.confidence,
      evidence: (a.evidence && a.evidence[0] && a.evidence[0].where) || null,
      label: (a.evidence && a.evidence[0] && a.evidence[0].match) || null,
    }));

  const agentSet = new Set(configAgents);
  for (const s of diskAgentSignals) agentSet.add(s.resolved);

  return {
    tracker: config?.tracker || inferred.tracker,
    ci: config?.ci || inferred.ci,
    language: config?.language || inferred.language,
    preset: config?.preset || inferred.preset,
    agents: [...agentSet],
    config_agents: [...configAgents],
    disk_agents: diskAgentSignals.map((s) => s.resolved),
    agent_signals: diskAgentSignals,
    source: {
      tracker: config?.tracker ? "config" : "disk",
      ci: config?.ci ? "config" : "disk",
      language: config?.language ? "config" : "disk",
      preset: config?.preset ? "config" : "disk",
    },
  };
}

/**
 * Decide what to tell the operator. When `.ship/config.yml` is present,
 * never propose a stack that contradicts it — instead propose additive
 * init commands that bring disk into agreement with config.
 *
 * @param {{inferred:object, existing:object, config:object|null, reconciled:object}} ctx
 */
function buildRecommendations(ctx) {
  const { inferred, existing, config, reconciled } = ctx;
  if (!config) return recommendations(inferred, existing);

  const configAgents = new Set(config.agents || []);
  const diskResolved = new Set(reconciled.disk_agents || []);
  const extras = [...diskResolved].filter((id) => !configAgents.has(id));
  const missingOnDisk = configAgents.size > 0 && diskResolved.size === 0;

  const diskPresent =
    existing.config_yml === "present" &&
    (existing.cache_dir === "present" || diskResolved.size > 0);

  if (missingOnDisk) {
    const list = [...configAgents].join(",");
    return [`shipctl init --bootstrap --copy-rules --agents ${list}`, "shipctl verify"];
  }

  if (extras.length) {
    const union = [...new Set([...configAgents, ...extras])].join(",");
    return [
      `shipctl init --agents ${union} --copy-rules`,
      "shipctl verify",
    ];
  }

  if (!diskPresent) {
    return ["shipctl init --bootstrap --copy-rules", "shipctl verify"];
  }

  return ["Config and disk agree. Run `shipctl verify`."];
}

function printHumanReport(report) {
  const {
    cwd,
    findings,
    inferred,
    preset_evidence,
    existing,
    recommendations: recs,
    config,
    reconciled,
  } = report;
  const out = [];
  out.push(`Ship doctor — inspecting ${cwd}`);
  out.push("");

  const topN = (arr, n, filterPresent = true) => {
    const pool = filterPresent ? arr.filter((e) => e.present && e.confidence > 0) : arr;
    return pool.slice(0, n);
  };

  const evToString = (ev) =>
    ev
      .map((e) => {
        const where = e.where && e.where !== "-" ? e.where : "";
        const match = e.match || "";
        return where ? `${where}${match ? ` (${match})` : ""}` : match;
      })
      .filter(Boolean)
      .join(", ");

  if (config) {
    // Reconciliation view — config is authoritative, disk is annotated.
    const diskPick = (arr) => {
      const pool = arr.filter((e) => e.present && e.confidence > 0);
      return pool[0] || null;
    };
    const fmtDisk = (entry) =>
      entry ? `${entry.id} (${entry.confidence.toFixed(2)})` : "no signal";
    const reconLine = (label, configVal, diskEntry) => {
      const left = configVal ? `${configVal} (config)` : "(unset)";
      const right = `disk: ${fmtDisk(diskEntry)}`;
      out.push(`${label.padEnd(12)} ${left} · ${right}`);
    };

    reconLine("Tracker:", config.tracker, diskPick(findings.trackers));
    reconLine("CI:", config.ci, diskPick(findings.ci));
    reconLine("Language:", config.language, diskPick(findings.language));

    const declared = config.agents || [];
    out.push(`${"Agents:".padEnd(12)} declared: ${declared.length ? declared.join(", ") : "(none)"}`);
    const signals = reconciled?.agent_signals || [];
    if (signals.length) {
      const parts = signals.map((s) => {
        const where = s.evidence && s.evidence !== "-" ? s.evidence : s.signal;
        return s.signal !== s.resolved
          ? `${where} (→ ${s.resolved} via config)`
          : s.resolved;
      });
      out.push(`${"".padEnd(12)} disk: ${parts.join(", ")}`);
    } else {
      out.push(`${"".padEnd(12)} disk: (none)`);
    }

    const presetEvidence =
      preset_evidence && preset_evidence.length ? preset_evidence.join(", ") : "no strong signals";
    const diskPresetNote = `disk inferred: ${inferred.preset} — evidence: ${presetEvidence}`;
    out.push(
      `${"Preset:".padEnd(12)} ${config.preset ? `${config.preset} (config)` : "(unset)"}  [${diskPresetNote}]`,
    );
    out.push("");
  } else {
    const categoryLine = (label, entries, fallback) => {
      const top = topN(entries, 5);
      if (!top.length) {
        out.push(`${label.padEnd(12)} ${fallback}`);
        return;
      }
      const head = top[0];
      const evStr = evToString(head.evidence);
      const headLine = `${label.padEnd(12)} ${head.id} (${head.confidence.toFixed(2)})${
        evStr ? ` · evidence: ${evStr}` : ""
      }`;
      out.push(headLine);
      for (const row of top.slice(1)) {
        out.push(`${"".padEnd(12)} ${row.id} (${row.confidence.toFixed(2)})`);
      }
    };

    categoryLine("Tracker:", findings.trackers, "none detected");
    categoryLine("CI:", findings.ci, "none detected");
    categoryLine("Language:", findings.language, "none detected");

    const agents = findings.agents.filter((a) => a.present && a.confidence > 0);
    const agentStr =
      agents
        .slice(0, 8)
        .map((a) => `${a.id} (${a.confidence.toFixed(2)})`)
        .join(", ") || "none";
    out.push(`${"Agents:".padEnd(12)} ${agentStr}`);
    out.push("");

    const presetEvidence =
      preset_evidence && preset_evidence.length ? preset_evidence.join(", ") : "no strong signals";
    out.push(`Inferred preset:  ${inferred.preset} (evidence: ${presetEvidence})`);
    out.push("");
  }

  out.push("Existing Ship artifacts:");
  out.push(`  .ship/config.yml       ${existing.config_yml}`);
  out.push(`  .ship/cache/           ${existing.cache_dir}`);
  out.push(`  .ship/inventory.json   ${existing.inventory_json}`);
  out.push(`  .cursor/rules/ship-*   ${existing.cursor_ship_rules}`);
  out.push("");

  out.push("Recommendations:");
  recs.forEach((r, i) => out.push(`  ${i + 1}. ${r}`));
  out.push("");

  const nextCmd =
    recs.find((r) => r.startsWith("shipctl init")) || recs[0] || "";
  out.push(`Next:       ${nextCmd}`);

  process.stdout.write(`${out.join("\n")}\n`);
}

async function writeInventoryFile(cwd, report) {
  const body = {
    version: 1,
    detected_at: report.detected_at,
    cwd: report.cwd,
    findings: report.findings,
    inferred: report.inferred,
  };

  // Prefer the real config-io module when available (race with parallel agent);
  // fall back to node:fs so doctor can always ship its inventory.
  let io = null;
  try {
    io = await import("../config/io.mjs");
  } catch {
    io = null;
  }

  const absDir = path.join(cwd, ".ship");
  const absPath = path.join(absDir, "inventory.json");

  if (io && typeof io.findShipRoot === "function") {
    // Honour the existing .ship/ location if config was already initialised
    // nearby; otherwise fall through to the cwd-local path.
    try {
      const root = io.findShipRoot(cwd);
      if (root) {
        const p = path.join(root, ".ship", "inventory.json");
        fs.mkdirSync(path.dirname(p), { recursive: true });
        const tmp = `${p}.tmp`;
        fs.writeFileSync(tmp, `${JSON.stringify(body, null, 2)}\n`, "utf8");
        fs.renameSync(tmp, p);
        return p;
      }
    } catch {
      // Ignore and fall through to the direct fs write below.
    }
  }

  fs.mkdirSync(absDir, { recursive: true });
  const tmp = `${absPath}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(body, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, absPath);
  return absPath;
}
