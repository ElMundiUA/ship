import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { detectAgentTargets } from "../detect.mjs";
import { MARKER, cursorRuleMdc, markdownSection, standaloneDoc } from "../templates.mjs";

const END_MARKER = "<!-- ship-cli:end methodology-api -->";

/**
 * @param {{ baseUrl: string; yes: boolean; force: boolean; dryRun: boolean; json: boolean }} ctx
 * @param {string[]} args
 */
export async function initCommand(ctx, args) {
  let cwd = process.cwd();
  /** @type {string[]} */
  let only = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--cwd" && args[i + 1]) {
      cwd = path.resolve(args[++i]);
      continue;
    }
    if (a === "--only" && args[i + 1]) {
      only.push(args[++i]);
      continue;
    }
  }

  let targets = detectAgentTargets(cwd);
  if (only.includes("cursor") && !targets.some((t) => t.id === "cursor")) {
    targets.push({
      id: "cursor",
      label: "Cursor (`.cursor/rules/` will be created if missing)",
      paths: [path.join(cwd, ".cursor", "rules", "ship-methodology-api.mdc")],
    });
  }
  if (only.length) {
    const allowed = new Set(["cursor", "agents-md", "claude-md", "codex", "copilot"]);
    for (const o of only) {
      if (!allowed.has(o)) {
        console.error(`init: unknown --only ${o}. Allowed: ${[...allowed].join(", ")}`);
        process.exit(1);
      }
    }
    targets = targets.filter((t) => only.includes(t.id));
    if (!targets.length) {
      console.error("init: no agent targets matched --only (markers missing in this repo).");
      process.exit(1);
    }
  }

  /** @type {{ id: string; label: string; action: string }[]} */
  const plan = [];

  if (targets.some((t) => t.id === "cursor")) {
    const rulesDir = path.join(cwd, ".cursor", "rules");
    const file = path.join(rulesDir, "ship-methodology-api.mdc");
    plan.push({ id: "cursor", label: "Cursor rule", action: `write ${path.relative(cwd, file)}` });
  }
  for (const t of targets) {
    if (t.id === "agents-md") {
      plan.push({ id: "agents-md", label: t.label, action: `append section → ${t.paths[0]}` });
    }
    if (t.id === "claude-md") {
      plan.push({ id: "claude-md", label: t.label, action: `append section → ${t.paths[0]}` });
    }
    if (t.id === "codex") {
      plan.push({ id: "codex", label: t.label, action: `write ${path.relative(cwd, t.paths[0])}` });
    }
    if (t.id === "copilot") {
      plan.push({ id: "copilot", label: t.label, action: `append section → ${t.paths[0]}` });
    }
  }

  const standalonePath = path.join(cwd, "SHIP_AGENT_API.md");
  if (!targets.length) {
    plan.push({
      id: "standalone",
      label: "Standalone reference (no agent markers in repo)",
      action: `write ${path.relative(cwd, standalonePath)}`,
    });
  }

  if (ctx.json) {
    console.log(JSON.stringify({ cwd, baseUrl: ctx.baseUrl, plan }, null, 2));
    return;
  }

  console.log(`Repository: ${cwd}`);
  console.log(`API base URL in injected docs: ${ctx.baseUrl}\n`);
  console.log("Planned changes:");
  for (const p of plan) console.log(`  - [${p.id}] ${p.action}`);
  console.log("");

  if (ctx.dryRun) {
    console.log("(dry-run: no files written)");
    return;
  }

  if (!ctx.yes) {
    if (!input.isTTY || !output.isTTY) {
      console.error("init: not a TTY; re-run with --yes or use --dry-run to preview.");
      process.exit(1);
    }
    const rl = readline.createInterface({ input, output });
    const ans = (await rl.question("Apply these changes? [y/N] ")).trim().toLowerCase();
    rl.close();
    if (ans !== "y" && ans !== "yes") {
      console.log("Aborted.");
      return;
    }
  }

  for (const t of targets) {
    if (t.id === "cursor") {
      await writeCursorRule(cwd, ctx);
    }
    if (t.id === "agents-md") {
      await appendOrWrite(t.paths[0], markdownSection(ctx.baseUrl), ctx);
    }
    if (t.id === "claude-md") {
      await appendOrWrite(t.paths[0], markdownSection(ctx.baseUrl), ctx);
    }
    if (t.id === "codex") {
      await writeNew(t.paths[0], standaloneDoc(ctx.baseUrl), ctx);
    }
    if (t.id === "copilot") {
      await appendOrWrite(t.paths[0], markdownSection(ctx.baseUrl), ctx);
    }
  }

  if (!targets.length) {
    await writeNew(standalonePath, standaloneDoc(ctx.baseUrl), ctx);
  }

  console.log("Done.");
}

/**
 * @param {string} cwd
 * @param {{ force: boolean; dryRun: boolean }} ctx
 */
async function writeCursorRule(cwd, ctx) {
  const rulesDir = path.join(cwd, ".cursor", "rules");
  const file = path.join(rulesDir, "ship-methodology-api.mdc");
  const body = cursorRuleMdc(ctx.baseUrl);
  if (fs.existsSync(file) && fs.readFileSync(file, "utf8").includes(MARKER) && !ctx.force) {
    console.log(`skip (exists): ${file}`);
    return;
  }
  if (ctx.dryRun) return;
  fs.mkdirSync(rulesDir, { recursive: true });
  fs.writeFileSync(file, body, "utf8");
  console.log(`wrote ${file}`);
}

/**
 * @param {string} filePath
 * @param {string} section
 * @param {{ force: boolean; dryRun: boolean }} ctx
 */
async function appendOrWrite(filePath, section, ctx) {
  if (ctx.dryRun) return;
  let prev = "";
  if (fs.existsSync(filePath)) prev = fs.readFileSync(filePath, "utf8");
  if (prev.includes(MARKER) && !ctx.force) {
    console.log(`skip (already injected): ${filePath}`);
    return;
  }
  if (prev.includes(MARKER) && ctx.force) {
    prev = stripInjectedBlock(prev);
  }
  const block = `${section}\n${END_MARKER}\n`;
  const next = prev.replace(/\s+$/, "") + (prev ? "\n" : "") + block;
  fs.writeFileSync(filePath, next, "utf8");
  console.log(`updated ${filePath}`);
}

/**
 * @param {string} filePath
 * @param {string} body
 * @param {{ force: boolean; dryRun: boolean }} ctx
 */
async function writeNew(filePath, body, ctx) {
  if (ctx.dryRun) return;
  if (fs.existsSync(filePath) && fs.readFileSync(filePath, "utf8").includes(MARKER) && !ctx.force) {
    console.log(`skip (exists): ${filePath}`);
    return;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, body, "utf8");
  console.log(`wrote ${filePath}`);
}

/** @param {string} prev */
function stripInjectedBlock(prev) {
  const start = prev.indexOf(MARKER);
  if (start === -1) return prev;
  const end = prev.indexOf(END_MARKER, start);
  if (end === -1) return prev.slice(0, start).replace(/\n{3,}$/, "\n\n");
  return (prev.slice(0, start) + prev.slice(end + END_MARKER.length)).replace(/\n{3,}/g, "\n\n");
}
