import fs from "node:fs";
import path from "node:path";
import { readConfig, findShipRoot } from "../config/io.mjs";
import { allChecks, runChecks, summarize } from "../verify/registry.mjs";

const SEVERITY_ORDER = { info: 0, warn: 1, error: 2 };

function parseArgs(argv) {
  const out = {
    cwd: null,
    check: /** @type {string[]} */ ([]),
    json: false,
    noNetwork: false,
    severity: "info",
    help: false,
  };
  const copy = [...argv];
  while (copy.length) {
    const a = copy.shift();
    if (a === "--help" || a === "-h") { out.help = true; continue; }
    if (a === "--json") { out.json = true; continue; }
    if (a === "--no-network") { out.noNetwork = true; continue; }
    if (a === "--cwd" && copy.length) { out.cwd = copy.shift(); continue; }
    if (a && a.startsWith("--cwd=")) { out.cwd = a.slice("--cwd=".length); continue; }
    if (a === "--check" && copy.length) {
      for (const s of String(copy.shift()).split(",")) {
        const id = s.trim();
        if (id) out.check.push(id);
      }
      continue;
    }
    if (a && a.startsWith("--check=")) {
      for (const s of a.slice("--check=".length).split(",")) {
        const id = s.trim();
        if (id) out.check.push(id);
      }
      continue;
    }
    if (a === "--severity" && copy.length) { out.severity = copy.shift(); continue; }
    if (a && a.startsWith("--severity=")) { out.severity = a.slice("--severity=".length); continue; }
    // Silently ignore unknown flags so globals (--base-url, --json) don't blow up.
  }
  if (!["info", "warn", "error"].includes(out.severity)) {
    throw new Error(`verify: unknown --severity '${out.severity}'. Expected: info|warn|error`);
  }
  return out;
}

function printHelp() {
  console.log(`shipctl verify — post-adoption liveness check for a Ship repo.

USAGE
  shipctl verify [--cwd DIR] [--check <id,...>] [--no-network]
                 [--severity info|warn|error] [--json]

OPTIONS
  --cwd DIR            Target repo root (defaults to cwd / nearest .ship/).
  --check <id,...>     Run only the listed check ids (csv or repeated).
  --no-network         Skip checks in the 'network' category.
  --severity <level>   Filter displayed rows:
                         info  (default)  — show all checks (pass/warn/fail/skip)
                         warn             — show warn + fail only
                         error            — show fail only
  --json               Machine-readable output: {checks:[...], summary:{...}}.

EXIT CODE
  0 when no checks returned 'fail' (warnings do not fail).
  1 when at least one check failed.

AVAILABLE CHECKS
${allChecks().map((c) => `  ${c.id.padEnd(22)} ${c.description}`).join("\n")}
`);
}

function loadConfig(cwd) {
  try {
    const { config } = readConfig(cwd);
    return config;
  } catch {
    return null;
  }
}

function loadInventory(cwd) {
  const root = findShipRoot(cwd) || cwd;
  const invPath = path.join(root, ".ship", "inventory.json");
  if (!fs.existsSync(invPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(invPath, "utf8"));
  } catch {
    return null;
  }
}

function pickByStatus(rows, severity) {
  if (severity === "info") return rows;
  if (severity === "warn") return rows.filter((r) => r.status === "warn" || r.status === "fail");
  return rows.filter((r) => r.status === "fail");
}

function badge(status) {
  return `[${status}]`;
}

function pad(s, n) {
  s = String(s);
  if (s.length >= n) return s;
  return s + " ".repeat(n - s.length);
}

/**
 * @param {{json:boolean, yes:boolean, force:boolean, dryRun:boolean, baseUrl?:string}} ctx
 * @param {string[]} args
 */
export async function verifyCommand(ctx, args) {
  let parsed;
  try {
    parsed = parseArgs(args);
  } catch (e) {
    console.error(e.message);
    process.exit(2);
    return;
  }
  if (parsed.help) { printHelp(); return; }
  if (ctx && ctx.json) parsed.json = true;

  const rawCwd = parsed.cwd || process.cwd();
  const resolvedRoot = findShipRoot(rawCwd) || path.resolve(rawCwd);

  const config = loadConfig(resolvedRoot);
  const inventory = loadInventory(resolvedRoot);
  const baseUrl = (ctx && ctx.baseUrl)
    || (config && config.api && config.api.base_url)
    || process.env.SHIP_API_BASE
    || "https://ship.elmundi.com";

  const checkCtx = {
    cwd: resolvedRoot,
    config,
    inventory,
    baseUrl,
    logger: (msg) => { if (!parsed.json) process.stderr.write(`${msg}\n`); },
  };

  const rows = await runChecks(checkCtx, {
    filter: parsed.check.length ? parsed.check : null,
    noNetwork: parsed.noNetwork,
  });
  const summary = summarize(rows);
  const exitCode = summary.fail > 0 ? 1 : 0;

  if (parsed.json) {
    process.stdout.write(
      JSON.stringify(
        {
          cwd: resolvedRoot,
          checks: rows,
          summary,
          exit_code: exitCode,
        },
        null,
        2,
      ) + "\n",
    );
    process.exit(exitCode);
    return;
  }

  const header = [`Ship verify — ${resolvedRoot}`, ""];
  const visible = pickByStatus(rows, parsed.severity);
  const idWidth = Math.max(
    14,
    ...visible.map((r) => r.id.length),
  );

  const body = visible.map((r) => `${badge(r.status)} ${pad(r.id, idWidth)}  ${r.detail}`);
  const footer = [
    "",
    `${summary.total} check${summary.total === 1 ? "" : "s"} total: ${summary.pass} pass, ${summary.warn} warn, ${summary.fail} fail, ${summary.skip} skip`,
    `Exit code: ${exitCode}${summary.fail ? " (any fail)" : ""}`,
  ];
  if (!visible.length) {
    body.push(`(no checks match --severity ${parsed.severity})`);
  }
  process.stdout.write(`${header.concat(body, footer).join("\n")}\n`);
  process.exit(exitCode);
}
