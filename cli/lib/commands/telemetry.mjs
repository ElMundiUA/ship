import fs from "node:fs";
import readline from "node:readline";
import { randomUUID } from "node:crypto";
import {
  findShipRoot,
  readConfig,
  writeConfig,
  readState,
  writeState,
} from "../config/io.mjs";
import { validateConfig } from "../config/schema.mjs";
import {
  outboxPath,
  listEvents,
  countEvents,
  clearEvents,
  writeAllEvents,
  ALLOWED_EVENT_TYPES,
} from "../telemetry/outbox.mjs";
import {
  postTelemetry,
  exportTelemetry,
  deleteTelemetry,
} from "../http.mjs";

const KNOWN_SCOPES = ["artifact_usage", "improvement_drafts", "errors"];

function parseArgs(rest) {
  const out = {
    cwd: process.cwd(),
    yes: false,
    dryRun: false,
    out: null,
    scope: null,
    limit: null,
    positional: [],
  };
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
    if (a === "--yes" || a === "-y") {
      out.yes = true;
      copy.shift();
      continue;
    }
    if (a === "--dry-run") {
      out.dryRun = true;
      copy.shift();
      continue;
    }
    if (a === "--out" && copy[1]) {
      copy.shift();
      out.out = String(copy.shift());
      continue;
    }
    if (a.startsWith("--out=")) {
      out.out = a.slice("--out=".length);
      copy.shift();
      continue;
    }
    if (a === "--scope" && copy[1]) {
      copy.shift();
      out.scope = String(copy.shift());
      continue;
    }
    if (a.startsWith("--scope=")) {
      out.scope = a.slice("--scope=".length);
      copy.shift();
      continue;
    }
    if (a === "--limit" && copy[1]) {
      copy.shift();
      out.limit = parseInt(copy.shift(), 10);
      continue;
    }
    if (a.startsWith("--limit=")) {
      out.limit = parseInt(a.slice("--limit=".length), 10);
      copy.shift();
      continue;
    }
    out.positional.push(copy.shift());
  }
  return out;
}

function requireShipRoot(cwd) {
  const root = findShipRoot(cwd);
  if (!root) {
    console.error(".ship/ not found. Run 'shipctl config init' first.");
    process.exit(10);
  }
  return root;
}

function resolveBaseUrl(ctx, config) {
  return (
    ctx?.baseUrl ||
    process.env.SHIP_API_BASE ||
    config?.api?.base_url ||
    "https://ship.elmundi.com"
  ).replace(/\/$/, "");
}

async function promptConfirm(msg) {
  if (!process.stdin.isTTY) return false;
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  try {
    const answer = await new Promise((resolve) => rl.question(`${msg} [y/N] `, resolve));
    return /^y(es)?$/i.test(String(answer || "").trim());
  } finally {
    rl.close();
  }
}

async function promptLine(msg) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  try {
    return await new Promise((resolve) => rl.question(msg, resolve));
  } finally {
    rl.close();
  }
}

function saveConfig(root, config) {
  const { filePath } = readConfig(root);
  const valid = validateConfig(config);
  if (!valid.ok) {
    for (const e of valid.errors) console.error(e);
    process.exit(10);
  }
  writeConfig(filePath, config);
}

function printStatus(root) {
  const { config } = readConfig(root);
  const { state } = readState(root);
  const share = config.telemetry?.share === true;
  const id = config.telemetry?.anonymous_id || "(none)";
  const scope = config.telemetry?.scope || {};
  const scopeStr = KNOWN_SCOPES.map((k) => `${k}=${scope[k] === true}`).join(",");
  const pending = countEvents(root);
  const last = state.last_flush_at || "never";
  console.log(`share=${share}`);
  console.log(`anonymous_id=${id}`);
  console.log(`scope=${scopeStr}`);
  console.log(`outbox_pending=${pending}`);
  console.log(`last_flush_at=${last}`);
}

async function cmdOn(root, args) {
  const { config } = readConfig(root);
  config.telemetry = config.telemetry || {};
  config.telemetry.scope = config.telemetry.scope || {
    artifact_usage: true,
    improvement_drafts: true,
    errors: false,
  };

  if (args.scope) {
    const parts = args.scope.split(",").map((s) => s.trim()).filter(Boolean);
    for (const p of parts) {
      if (!KNOWN_SCOPES.includes(p)) {
        console.error(`unknown scope: ${p}; allowed=${KNOWN_SCOPES.join(",")}`);
        process.exit(1);
      }
    }
    for (const p of parts) config.telemetry.scope[p] = true;
  }

  if (!args.yes) {
    const ok = await promptConfirm(
      "Enable anonymous telemetry (artifact usage + feedback metadata)?",
    );
    if (!ok) {
      console.error("aborted.");
      process.exit(1);
    }
  }
  config.telemetry.share = true;
  if (!config.telemetry.anonymous_id) {
    config.telemetry.anonymous_id = randomUUID();
  }
  saveConfig(root, config);
  console.log(
    `telemetry.share=true anonymous_id=${config.telemetry.anonymous_id} scope=${KNOWN_SCOPES.map(
      (k) => `${k}=${config.telemetry.scope[k] === true}`,
    ).join(",")}`,
  );
}

function cmdOff(root) {
  const { config } = readConfig(root);
  config.telemetry = config.telemetry || {};
  config.telemetry.share = false;
  config.telemetry.scope = {
    artifact_usage: false,
    improvement_drafts: false,
    errors: false,
  };
  saveConfig(root, config);
  console.log("telemetry.share=false");
}

function cmdShowId(root) {
  const { config } = readConfig(root);
  const id = config.telemetry?.anonymous_id;
  if (!id) {
    console.error("no anonymous_id set. Run 'shipctl telemetry on' to generate one.");
    process.exit(1);
  }
  console.log(id);
}

function cmdResetId(root) {
  const { config } = readConfig(root);
  config.telemetry = config.telemetry || {};
  config.telemetry.anonymous_id = randomUUID();
  saveConfig(root, config);
  console.log(config.telemetry.anonymous_id);
}

async function cmdFlush(ctx, root, args) {
  const { config } = readConfig(root);
  if (config.telemetry?.share !== true) {
    console.log("telemetry disabled; nothing to send");
    return;
  }
  const events = listEvents(root);
  if (events.length === 0) {
    console.log("flushed 0 events, 0 failed");
    return;
  }
  if (args.dryRun) {
    console.log(`would flush ${events.length} events to ${resolveBaseUrl(ctx, config)}/telemetry`);
    return;
  }

  const baseUrl = resolveBaseUrl(ctx, config);
  let flushed = 0;
  let failed = 0;
  const pending = [];

  for (let i = 0; i < events.length; i += 100) {
    const batch = events.slice(i, i + 100);
    const stripped = batch.map((ev) => ({
      type: ev.type,
      anonymous_id: ev.anonymous_id || config.telemetry.anonymous_id,
      timestamp: ev.timestamp,
      payload: ev.payload || {},
    }));
    const res = await postTelemetry(baseUrl, stripped);
    if (res.ok) {
      flushed += batch.length;
    } else {
      failed += batch.length;
      pending.push(...batch);
    }
  }

  writeAllEvents(root, pending);

  if (flushed > 0) {
    try {
      const { state } = readState(root);
      writeState(root, { ...state, last_flush_at: new Date().toISOString() });
    } catch {
      // non-fatal
    }
  }

  console.log(`flushed ${flushed} events, ${failed} failed`);
  if (failed > 0) process.exit(20);
}

async function cmdExport(ctx, root, args) {
  const { config } = readConfig(root);
  const id = config.telemetry?.anonymous_id;
  if (!id) {
    console.error("no anonymous_id set.");
    process.exit(1);
  }
  const baseUrl = resolveBaseUrl(ctx, config);
  const data = await exportTelemetry(baseUrl, id);
  const json = JSON.stringify(data, null, 2);
  if (args.out) {
    fs.writeFileSync(args.out, json + "\n", "utf8");
    console.log(`wrote ${args.out}`);
  } else {
    console.log(json);
  }
}

async function cmdDeleteMyData(ctx, root) {
  const { config } = readConfig(root);
  const id = config.telemetry?.anonymous_id;
  if (!id) {
    console.error("no anonymous_id set; nothing to delete.");
    process.exit(1);
  }
  if (!process.stdin.isTTY) {
    console.error("delete-my-data requires an interactive terminal.");
    process.exit(1);
  }
  const typed = (
    await promptLine(`Are you sure? Type the anonymous_id to confirm: `)
  ).trim();
  if (typed !== id) {
    console.error("mismatch; aborted.");
    process.exit(1);
  }
  const baseUrl = resolveBaseUrl(ctx, config);
  const data = await deleteTelemetry(baseUrl, id);
  console.log(JSON.stringify({ deleted: data?.deleted ?? 0 }));
}

function summarizePayload(payload) {
  if (!payload || typeof payload !== "object") return "";
  const parts = [];
  for (const k of ["kind", "id", "version", "agent", "source", "updates_count", "failures_count"]) {
    if (payload[k] !== undefined) parts.push(`${k}=${JSON.stringify(payload[k])}`);
  }
  if (parts.length === 0) {
    const first = Object.keys(payload).slice(0, 3);
    for (const k of first) parts.push(`${k}=${JSON.stringify(payload[k])}`);
  }
  return parts.join(" ");
}

function cmdBuffer(root, args) {
  const limit = Number.isFinite(args.limit) && args.limit > 0 ? args.limit : 20;
  const events = listEvents(root);
  const tail = events.slice(-limit);
  for (const ev of tail) {
    console.log(
      `${ev.timestamp || "-"}  ${ev.type || "-"}  ${summarizePayload(ev.payload)}`,
    );
  }
  console.log(`(${tail.length}/${events.length} shown; outbox=${outboxPath(root)})`);
}

export async function telemetryCommand(ctx, rest) {
  const args = parseArgs(rest);
  const sub = args.positional[0];
  const root = requireShipRoot(args.cwd);

  switch (sub) {
    case "status":
      printStatus(root);
      return;
    case "on":
      await cmdOn(root, args);
      return;
    case "off":
      cmdOff(root);
      return;
    case "show-id":
      cmdShowId(root);
      return;
    case "reset-id":
      cmdResetId(root);
      return;
    case "flush":
      await cmdFlush(ctx, root, args);
      return;
    case "export":
      await cmdExport(ctx, root, args);
      return;
    case "delete-my-data":
      await cmdDeleteMyData(ctx, root);
      return;
    case "buffer":
      cmdBuffer(root, args);
      return;
    default:
      console.error(
        `usage: shipctl telemetry <status|on|off|show-id|reset-id|flush|export|delete-my-data|buffer>\nallowed event types: ${ALLOWED_EVENT_TYPES.join(", ")}`,
      );
      process.exit(2);
  }
}
