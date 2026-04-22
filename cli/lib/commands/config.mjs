import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";
import {
  DEFAULT_CONFIG,
  ensureAnonymousId,
  findShipRoot,
  readConfig,
  writeConfig,
  writeState,
  defaultState,
  SHIP_DIR,
  CONFIG_REL,
  STATE_REL,
} from "../config/io.mjs";
import { validateConfig } from "../config/schema.mjs";

function parseConfigArgs(rest) {
  const out = { cwd: null, positional: [] };
  const copy = [...rest];
  while (copy.length) {
    const a = copy[0];
    if (a === "--cwd" && copy[1]) {
      copy.shift();
      out.cwd = String(copy.shift());
      continue;
    }
    if (a.startsWith("--cwd=")) {
      out.cwd = a.slice("--cwd=".length);
      copy.shift();
      continue;
    }
    out.positional.push(copy.shift());
  }
  out.cwd = out.cwd || process.cwd();
  return out;
}

function ensureGitignoreEntry(shipRoot) {
  const giPath = path.join(shipRoot, ".gitignore");
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
  if (toAppend.length === 0) return { giPath, changed: false };
  const prefix = current.length === 0 || current.endsWith("\n") ? "" : "\n";
  const tail = current.length === 0 ? `${toAppend.join("\n")}\n` : `${prefix}${toAppend.join("\n")}\n`;
  fs.writeFileSync(giPath, current + tail, "utf8");
  return { giPath, changed: true };
}

function initCmd(rest) {
  const { cwd } = parseConfigArgs(rest);
  const root = path.resolve(cwd);
  const shipDir = path.join(root, SHIP_DIR);
  const filePath = path.join(root, CONFIG_REL);

  if (fs.existsSync(filePath)) {
    console.error(`exists: ${filePath}`);
    process.exit(1);
  }

  fs.mkdirSync(shipDir, { recursive: true });
  const config = DEFAULT_CONFIG();
  ensureAnonymousId(config);

  writeConfig(filePath, config);
  writeState(root, defaultState());

  const cacheDir = path.join(shipDir, "cache");
  fs.mkdirSync(cacheDir, { recursive: true });
  const keep = path.join(cacheDir, ".gitkeep");
  if (!fs.existsSync(keep)) fs.writeFileSync(keep, "", "utf8");

  const { giPath, changed } = ensureGitignoreEntry(root);

  console.log(`created: ${filePath}`);
  console.log(`created: ${path.join(root, STATE_REL)}`);
  console.log(`created: ${cacheDir}/`);
  console.log(`${changed ? "updated" : "ok     "}: ${giPath}`);
}

function getAtPath(obj, dottedKey) {
  const parts = parsePath(dottedKey);
  let cur = obj;
  for (const p of parts) {
    if (cur == null) return undefined;
    if (typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

/**
 * Split a dotted key, preserving `<kind>/<id>` segments under artifacts.pins.
 * Example: artifacts.pins.pattern/role-developer → ["artifacts","pins","pattern/role-developer"]
 */
function parsePath(dottedKey) {
  const raw = dottedKey.split(".");
  const out = [];
  for (let i = 0; i < raw.length; i++) {
    if (
      out.length === 2 &&
      out[0] === "artifacts" &&
      out[1] === "pins"
    ) {
      out.push(raw.slice(i).join("."));
      break;
    }
    out.push(raw[i]);
  }
  return out;
}

function setAtPath(obj, dottedKey, value) {
  const parts = parsePath(dottedKey);
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    if (cur[p] == null || typeof cur[p] !== "object") cur[p] = {};
    cur = cur[p];
  }
  cur[parts[parts.length - 1]] = value;
}

function parseValue(raw) {
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw === "null") return null;
  if (/^-?\d+$/.test(raw)) return Number(raw);
  if (/^-?\d+\.\d+$/.test(raw)) return Number(raw);
  const trimmed = raw.trim();
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    const inner = trimmed.slice(1, -1).trim();
    if (inner.length === 0) return [];
    return inner.split(",").map((x) => parseValue(x.trim().replace(/^['"]|['"]$/g, "")));
  }
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return raw;
}

function getCmd(rest) {
  const { cwd, positional } = parseConfigArgs(rest);
  const key = positional[0];
  if (!key) {
    console.error("usage: shipctl config get <dotted.key>");
    process.exit(1);
  }
  const { config } = readConfig(cwd);
  const val = getAtPath(config, key);
  if (val === undefined) {
    console.error(`unknown key: ${key}`);
    process.exit(1);
  }
  if (Array.isArray(val) || (val !== null && typeof val === "object")) {
    console.log(JSON.stringify(val));
  } else {
    console.log(String(val));
  }
}

function setCmd(rest) {
  const { cwd, positional } = parseConfigArgs(rest);
  const [key, ...valueParts] = positional;
  if (!key || valueParts.length === 0) {
    console.error("usage: shipctl config set <dotted.key> <value>");
    process.exit(1);
  }
  const raw = valueParts.join(" ");
  const value = parseValue(raw);
  const { config, filePath } = readConfig(cwd);
  setAtPath(config, key, value);

  const res = validateConfig(config);
  if (!res.ok) {
    for (const e of res.errors) console.error(e);
    process.exit(10);
  }
  for (const w of res.warnings) console.error(`warn: ${w}`);

  writeConfig(filePath, config);
  console.log(`${key} = ${JSON.stringify(value)}`);
}

function validateCmd(rest) {
  const { cwd } = parseConfigArgs(rest);
  const { config, filePath } = readConfig(cwd);
  const res = validateConfig(config);
  for (const w of res.warnings) console.error(`warn: ${w}`);
  if (!res.ok) {
    for (const e of res.errors) console.error(e);
    process.exit(10);
  }
  console.log(`ok: ${filePath}`);
}

function showCmd(rest) {
  const { cwd } = parseConfigArgs(rest);
  const { config } = readConfig(cwd);
  process.stdout.write(YAML.stringify(config, { lineWidth: 0, indent: 2 }));
}

function pathCmd(rest) {
  const { cwd } = parseConfigArgs(rest);
  const root = findShipRoot(cwd);
  if (!root) {
    console.log("not found");
    process.exit(1);
  }
  console.log(path.join(root, CONFIG_REL));
}

export async function configCommand(_ctx, rest) {
  const [sub, ...tail] = rest;
  if (!sub || sub === "-h" || sub === "--help" || sub === "help") {
    console.log(`shipctl config <subcommand>

Subcommands:
  init [--cwd DIR]            Create .ship/config.yml + state.json + cache/.
  get <dotted.key>            Print value.
  set <dotted.key> <value>    Update value (validates; atomic write).
  validate                    Validate .ship/config.yml; exit 10 on errors.
  show                        Pretty-print effective YAML.
  path                        Print absolute path to config file.
`);
    return;
  }
  switch (sub) {
    case "init":
      return initCmd(tail);
    case "get":
      return getCmd(tail);
    case "set":
      return setCmd(tail);
    case "validate":
      return validateCmd(tail);
    case "show":
      return showCmd(tail);
    case "path":
      return pathCmd(tail);
    default:
      console.error(`unknown subcommand: config ${sub}`);
      process.exit(1);
  }
}
