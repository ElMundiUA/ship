import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Resolve the shipctl entry script (bin/shipctl.mjs) relative to this module.
 * Used when we fall back to spawning `shipctl <subcommand>` subprocesses.
 */
function shipctlBinPath() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "..", "..", "bin", "shipctl.mjs");
}

/**
 * @param {string[]} args
 */
function parseNewArgs(args) {
  /** @type {Record<string, any>} */
  const out = {
    name: null,
    here: false,
    preset: null,
    tracker: null,
    ci: null,
    agents: /** @type {string[]} */ ([]),
    language: null,
    channel: "stable",
    baseUrl: null,
    yes: false,
    force: false,
    dryRun: false,
    json: false,
    help: false,
    // tri-state: null = default (copy-rules enabled iff agents non-empty),
    //            true = forced on, false = opted out via --no-copy-rules.
    copyRules: null,
    // tri-state: null = default on, false = opted out via --no-bootstrap.
    bootstrap: null,
    telemetry: "off",
    extra: /** @type {string[]} */ ([]),
  };

  const copy = [...args];
  while (copy.length) {
    const a = copy.shift();
    if (a === "--here") { out.here = true; continue; }
    if (a === "--help" || a === "-h") { out.help = true; continue; }
    if (a === "--yes" || a === "-y") { out.yes = true; continue; }
    if (a === "--force") { out.force = true; continue; }
    if (a === "--dry-run") { out.dryRun = true; continue; }
    if (a === "--json") { out.json = true; continue; }
    if (a === "--copy-rules") { out.copyRules = true; continue; }
    if (a === "--no-copy-rules") { out.copyRules = false; continue; }
    if (a === "--bootstrap") { out.bootstrap = true; continue; }
    if (a === "--no-bootstrap") { out.bootstrap = false; continue; }
    if (a === "--preset" && copy.length) { out.preset = copy.shift(); continue; }
    if (a.startsWith("--preset=")) { out.preset = a.slice("--preset=".length); continue; }
    if (a === "--tracker" && copy.length) { out.tracker = copy.shift(); continue; }
    if (a.startsWith("--tracker=")) { out.tracker = a.slice("--tracker=".length); continue; }
    if (a === "--ci" && copy.length) { out.ci = copy.shift(); continue; }
    if (a.startsWith("--ci=")) { out.ci = a.slice("--ci=".length); continue; }
    if (a === "--base-url" && copy.length) { out.baseUrl = copy.shift(); continue; }
    if (a.startsWith("--base-url=")) { out.baseUrl = a.slice("--base-url=".length); continue; }
    if (a === "--agents" && copy.length) {
      for (const s of String(copy.shift()).split(",")) {
        const id = s.trim();
        if (id) out.agents.push(id);
      }
      continue;
    }
    if (a.startsWith("--agents=")) {
      for (const s of a.slice("--agents=".length).split(",")) {
        const id = s.trim();
        if (id) out.agents.push(id);
      }
      continue;
    }
    if (a === "--language" && copy.length) { out.language = copy.shift(); continue; }
    if (a.startsWith("--language=")) { out.language = a.slice("--language=".length); continue; }
    if (a === "--channel" && copy.length) { out.channel = copy.shift(); continue; }
    if (a.startsWith("--channel=")) { out.channel = a.slice("--channel=".length); continue; }
    if (a === "--telemetry" && copy.length) { out.telemetry = copy.shift(); continue; }
    if (a.startsWith("--telemetry=")) { out.telemetry = a.slice("--telemetry=".length); continue; }
    if (a && a.startsWith("--")) { out.extra.push(a); continue; }
    if (out.name == null) { out.name = a; continue; }
    out.extra.push(a);
  }
  return out;
}

function printNewHelp() {
  console.log(`shipctl new <name> — bootstrap a fresh repository with Ship wiring.

USAGE
  shipctl new <name> [options]
  shipctl new [--here] [options]

OPTIONS
  --here                 Initialise in the current directory instead of <name>/.
  --preset <id>          adoption-minimum|web-app|api-backend|mobile-app|cli|monorepo
  --tracker <id>         linear|jira|github-issues|azure-boards|clickup|spreadsheet|none
  --ci <id>              gh-actions|gitlab-ci|buildkite|circleci|azure-pipelines|jenkins|manual
  --agents <csv>         Comma-separated agent ids (e.g. cursor,codex,claude).
  --language <id>        ts|js|py|go|rust|java|kotlin|swift|dart|multi
  --channel <id>         stable|edge (written to api.channel; default stable).
  --base-url <url>       Override Ship API base URL (forwarded to 'init').
  --copy-rules           Forward to init. Default ON when agents are selected;
                         use --no-copy-rules to opt out.
  --no-copy-rules        Skip installing cached agent rule files on disk.
  --bootstrap            Forward --bootstrap to init. Default ON; use
                         --no-bootstrap to opt out.
  --no-bootstrap         Skip rendering CI/tracker scaffolding.
  --telemetry <on|off>   Default: off. Writes telemetry.share.
  --yes                  Non-interactive (assumed for --dry-run).
  --force                Reuse a non-empty target directory.
  --dry-run              Describe the plan without touching disk.
  --json                 Machine-readable summary.

Creates <name>/ (or reuses cwd with --here), runs 'git init -q', writes a
minimal README.md, seeds .ship/config.yml via 'shipctl config init', applies
the provided stack flags via 'shipctl config set', and then runs
'shipctl init --yes' for any selected agents.
`);
}

function dirIsEmpty(dir) {
  try {
    const entries = fs.readdirSync(dir);
    return entries.filter((e) => e !== ".DS_Store").length === 0;
  } catch {
    return true;
  }
}

function isGitRepo(dir) {
  return fs.existsSync(path.join(dir, ".git"));
}

function resolveTargetDir(args, cwd) {
  if (args.here) return path.resolve(cwd);
  if (!args.name) {
    throw new Error("new: missing <name>. Run 'shipctl new --help' for usage.");
  }
  return path.resolve(cwd, args.name);
}

/**
 * Run `shipctl <sub...>` via the same Node binary, capturing output for JSON mode.
 * @param {string[]} argv
 * @param {{capture?:boolean}} [opts]
 */
function runShipctl(argv, opts = {}) {
  const bin = shipctlBinPath();
  const res = spawnSync(process.execPath, [bin, ...argv], {
    stdio: opts.capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
  });
  if (res.error) throw res.error;
  return {
    status: typeof res.status === "number" ? res.status : 1,
    stdout: res.stdout || "",
    stderr: res.stderr || "",
  };
}

/**
 * Apply stack-level settings via `shipctl config set`.
 * Missing values are skipped.
 * @param {string} newDir
 * @param {ReturnType<typeof parseNewArgs>} a
 * @param {boolean} capture
 * @returns {{ok:boolean, applied:string[], errors:string[]}}
 */
function applyStackConfig(newDir, a, capture) {
  const applied = [];
  const errors = [];
  const set = (key, value) => {
    const res = runShipctl(["config", "set", key, String(value), "--cwd", newDir], {
      capture,
    });
    if (res.status !== 0) {
      errors.push(`${key}=${value}: ${(res.stderr || res.stdout).trim() || `exit ${res.status}`}`);
      return false;
    }
    applied.push(`${key}=${value}`);
    return true;
  };

  if (a.tracker) set("stack.tracker", a.tracker);
  if (a.ci) set("stack.ci", a.ci);
  if (a.preset) set("stack.preset", a.preset);
  if (a.language) set("stack.language", a.language);
  if (a.channel) set("api.channel", a.channel);
  if (a.agents.length) set("stack.agents", `[${a.agents.join(",")}]`);
  if (a.telemetry === "on") set("telemetry.share", "true");
  else if (a.telemetry === "off") set("telemetry.share", "false");

  return { ok: errors.length === 0, applied, errors };
}

/**
 * @param {{ baseUrl:string, yes:boolean, force:boolean, dryRun:boolean, json:boolean }} ctx
 * @param {string[]} args
 */
export async function newCommand(ctx, args) {
  const a = parseNewArgs(args);
  if (a.help) { printNewHelp(); return; }

  if (ctx) {
    if (ctx.json) a.json = true;
    if (ctx.yes) a.yes = true;
    if (ctx.force) a.force = true;
    if (ctx.dryRun) a.dryRun = true;
    // extractGlobalArgv in bin/shipctl.mjs strips `--base-url` out of argv
    // and stashes it on ctx. Fold it in here so the init subprocess gets
    // the same URL the caller handed to `shipctl new`.
    if (!a.baseUrl && ctx.baseUrl) a.baseUrl = ctx.baseUrl;
  }

  const cwd = process.cwd();
  let newDir;
  try {
    newDir = resolveTargetDir(a, cwd);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }

  const willCreate = !fs.existsSync(newDir);
  const alreadyGit = !willCreate && isGitRepo(newDir);
  const alreadyNonEmpty = !willCreate && !dirIsEmpty(newDir);
  const configAlready = fs.existsSync(path.join(newDir, ".ship", "config.yml"));

  if (!a.here && !willCreate && alreadyNonEmpty && !a.force) {
    console.error(
      `new: target directory is not empty: ${newDir}\n` +
        `Re-run with --force to reuse it, or choose a different <name>.`,
    );
    process.exit(1);
  }

  const plannedFiles = [
    { path: path.join(newDir, ".git"), reason: "git init" },
    { path: path.join(newDir, "README.md"), reason: "minimal stub" },
    { path: path.join(newDir, ".ship", "config.yml"), reason: "shipctl config init" },
  ];

  const initArgv = buildInitArgv(a, newDir);
  const runInit = shouldRunInit(a);

  const summary = {
    cwd,
    dir: newDir,
    created_dir: willCreate,
    reused_dir: !willCreate,
    here: a.here,
    git_init: !alreadyGit,
    readme: true,
    stack: {
      tracker: a.tracker,
      ci: a.ci,
      preset: a.preset,
      language: a.language,
      channel: a.channel,
      base_url: a.baseUrl,
      agents: a.agents,
      telemetry: a.telemetry,
      copy_rules:
        a.copyRules === true || (a.copyRules !== false && a.agents.length > 0),
      bootstrap: a.bootstrap !== false,
    },
    init_argv: initArgv,
    run_init: runInit,
    planned_files: plannedFiles.map((f) => path.relative(cwd, f.path) || f.path),
    next_steps: [
      `cd ${path.relative(cwd, newDir) || "."}`,
      "shipctl verify",
    ],
  };

  if (a.dryRun) {
    if (a.json) {
      console.log(JSON.stringify({ ...summary, dry_run: true }, null, 2));
      return;
    }
    console.log(
      `shipctl new (dry-run) — ${a.here ? "using current dir" : willCreate ? "would create" : "would reuse"}: ${newDir}`,
    );
    const show = (full) => {
      const rel = path.relative(cwd, full);
      return rel && !rel.startsWith("..") ? rel : full;
    };
    for (const f of plannedFiles) {
      console.log(`  plan: write ${show(f.path)} (${f.reason})`);
    }
    if (runInit) {
      console.log(`  plan: shipctl ${initArgv.join(" ")}`);
    }
    const stackLines = [];
    if (a.tracker) stackLines.push(`stack.tracker=${a.tracker}`);
    if (a.ci) stackLines.push(`stack.ci=${a.ci}`);
    if (a.preset) stackLines.push(`stack.preset=${a.preset}`);
    if (a.language) stackLines.push(`stack.language=${a.language}`);
    if (a.channel) stackLines.push(`api.channel=${a.channel}`);
    if (a.agents.length) stackLines.push(`stack.agents=[${a.agents.join(",")}]`);
    if (a.telemetry) stackLines.push(`telemetry.share=${a.telemetry === "on"}`);
    for (const s of stackLines) console.log(`  plan: shipctl config set ${s}`);
    console.log("(dry-run: no files written)");
    return;
  }

  const createdFiles = [];
  if (willCreate) fs.mkdirSync(newDir, { recursive: true });

  if (!alreadyGit) {
    const gitInit = spawnSync("git", ["init", "-q"], { cwd: newDir, encoding: "utf8" });
    if (gitInit.status !== 0) {
      const reason = (gitInit.stderr || "").trim() || `exit ${gitInit.status}`;
      console.error(`new: 'git init' failed in ${newDir}: ${reason}`);
      process.exit(1);
    }
    createdFiles.push(path.join(newDir, ".git"));
  }

  const readmePath = path.join(newDir, "README.md");
  if (!fs.existsSync(readmePath)) {
    const displayName = a.name || path.basename(newDir);
    fs.writeFileSync(
      readmePath,
      `# ${displayName}\n\nBootstrapped with shipctl (Ship methodology kit).\n`,
      "utf8",
    );
    createdFiles.push(readmePath);
  }

  const capture = !!a.json;
  const log = (msg) => { if (!a.json) console.log(msg); };

  if (!configAlready) {
    const res = runShipctl(["config", "init", "--cwd", newDir], { capture });
    if (res.status !== 0) {
      const out = (res.stderr || res.stdout).trim();
      console.error(
        `new: 'shipctl config init' exited with code ${res.status}${out ? `\n${out}` : ""}`,
      );
      process.exit(res.status);
    }
    createdFiles.push(path.join(newDir, ".ship", "config.yml"));
    if (!a.json) process.stdout.write(res.stdout || "");
  } else {
    log(`config: reusing existing ${path.join(newDir, ".ship", "config.yml")}`);
  }

  const stackResult = applyStackConfig(newDir, a, capture);
  if (!stackResult.ok) {
    console.error("new: failed to apply stack flags:");
    for (const e of stackResult.errors) console.error(`  - ${e}`);
    process.exit(10);
  }
  if (!a.json) {
    for (const s of stackResult.applied) console.log(`config set ${s}`);
  }

  let initStatus = 0;
  if (runInit) {
    const res = runShipctl(initArgv, { capture });
    initStatus = res.status;
    if (!a.json) process.stdout.write(res.stdout || "");
    if (res.status !== 0) {
      const out = (res.stderr || res.stdout).trim();
      console.error(
        `new: 'shipctl init' exited with code ${res.status}${out ? `\n${out}` : ""}\n` +
          `Re-run with the same flags to retry, or pass --no-bootstrap / --no-copy-rules to skip remote steps.`,
      );
      process.exit(res.status);
    }
  }

  const finalConfigPath = path.join(newDir, ".ship", "config.yml");
  const configExists = fs.existsSync(finalConfigPath);

  if (a.json) {
    console.log(
      JSON.stringify(
        {
          ...summary,
          dry_run: false,
          stack_set: stackResult.applied,
          created_files: createdFiles.map((p) => path.relative(cwd, p) || p),
          config_written: configExists,
          init_status: initStatus,
        },
        null,
        2,
      ),
    );
    return;
  }

  console.log("");
  console.log(`Done. Ship scaffolding in ${newDir}`);
  console.log("Next:");
  console.log(`  cd ${path.relative(cwd, newDir) || "."}`);
  console.log("  shipctl verify");
}

/**
 * Build the argv list for the `shipctl init` subprocess. Kept separate so
 * --dry-run can show the plan without executing. Forwards the full set of
 * stack flags (tracker, CI, preset, agents, language, channel, base-url,
 * telemetry) and defaults --copy-rules ON (when agents were selected) and
 * --bootstrap ON. Use --no-copy-rules / --no-bootstrap on `new` to opt out.
 *
 * @param {ReturnType<typeof parseNewArgs>} a
 * @param {string} newDir
 */
export function buildInitArgv(a, newDir) {
  const argv = ["init", "--cwd", newDir, "--yes"];
  if (a.agents.length) argv.push("--agents", a.agents.join(","));
  if (a.tracker) argv.push("--tracker", a.tracker);
  if (a.ci) argv.push("--ci", a.ci);
  if (a.preset) argv.push("--preset", a.preset);
  if (a.force) argv.push("--force");
  if (a.language) argv.push("--language", a.language);
  if (a.channel) argv.push("--channel", a.channel);
  if (a.telemetry) argv.push("--telemetry", a.telemetry);
  if (a.baseUrl) argv.push("--base-url", a.baseUrl);

  const wantCopyRules =
    a.copyRules === true || (a.copyRules !== false && a.agents.length > 0);
  if (wantCopyRules) argv.push("--copy-rules");

  const wantBootstrap = a.bootstrap !== false;
  if (wantBootstrap) argv.push("--bootstrap");

  if (a.json) argv.push("--json");
  return argv;
}

/**
 * Decide whether `shipctl new` needs to spawn `shipctl init` at all. init
 * does the real work (rule files on disk, CI scaffolding, telemetry prompt,
 * initial sync). If the caller said --no-bootstrap and passed no agents,
 * we skip the subprocess entirely.
 * @param {ReturnType<typeof parseNewArgs>} a
 */
function shouldRunInit(a) {
  if (a.agents.length > 0) return true;
  if (a.bootstrap !== false) return true;
  return false;
}
