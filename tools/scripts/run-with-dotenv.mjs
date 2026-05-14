#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

function stripInlineComment(value) {
  let quote = null;
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if ((ch === "'" || ch === '"') && (i === 0 || value[i - 1] !== "\\")) {
      quote = quote === ch ? null : quote ?? ch;
      continue;
    }
    if (ch === "#" && quote === null && (i === 0 || /\s/.test(value[i - 1]))) {
      return value.slice(0, i).trimEnd();
    }
  }
  return value.trimEnd();
}

function parseDotenv(path) {
  const env = {};
  const text = readFileSync(path, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = rawLine.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!match) continue;
    const [, key, rawValue] = match;
    let value = stripInlineComment(rawValue).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    env[key] = value.replace(/\\n/g, "\n");
  }
  return env;
}

const args = process.argv.slice(2);
const defaults = {};
const forced = {};
let separatorIndex = args.indexOf("--");

if (separatorIndex === -1) {
  console.error("usage: run-with-dotenv.mjs [--default KEY=VALUE] [--set KEY=VALUE] -- command [args...]");
  process.exit(2);
}

for (let i = 0; i < separatorIndex; i += 1) {
  const flag = args[i];
  const assignment = args[i + 1];
  if ((flag !== "--default" && flag !== "--set") || !assignment?.includes("=")) {
    console.error(`invalid option near ${flag}`);
    process.exit(2);
  }
  const [key, ...valueParts] = assignment.split("=");
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
    console.error(`invalid env key: ${key}`);
    process.exit(2);
  }
  if (flag === "--default") defaults[key] = valueParts.join("=");
  else forced[key] = valueParts.join("=");
  i += 1;
}

const command = args[separatorIndex + 1];
const commandArgs = args.slice(separatorIndex + 2);
if (!command) {
  console.error("missing command");
  process.exit(2);
}

// Layered env: committed baseline → developer overrides → CLI args.
//
// `.env.shared` carries the laptop-friendly defaults (local Postgres,
// local-mode auth, blank third-party keys) and is committed. `.env`
// stays gitignored and overrides anything the developer wants to
// retarget (point at Neon, switch to Auth0, plug in real OpenAI etc.).
// CLI --default fills only when the merged file still left a key
// blank; --set wins over everything.
const layered = {};
if (existsSync(".env.shared")) Object.assign(layered, parseDotenv(".env.shared"));
if (existsSync(".env")) Object.assign(layered, parseDotenv(".env"));
const env = { ...process.env, ...layered };
for (const [key, value] of Object.entries(defaults)) {
  if (!env[key]) env[key] = value;
}
Object.assign(env, forced);

const child = spawn(command, commandArgs, { env, stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    child.kill(signal);
  });
}
child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(128 + (signal === "SIGINT" ? 2 : 15));
    return;
  }
  process.exit(code ?? 0);
});
