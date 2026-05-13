import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { spawn } from "node:child_process";
import {
  findShipRoot,
  readConfig,
} from "../config/io.mjs";
import {
  draftsDir,
  createDraft,
  listDrafts,
  readDraft,
  removeDraft,
  moveDraftToSent,
} from "../feedback/drafts.mjs";
import { postFeedback } from "../http.mjs";
import { appendEvent } from "../telemetry/outbox.mjs";

const ALLOWED_KINDS = ["collection"];

function parseArgs(rest) {
  const out = {
    cwd: process.cwd(),
    yes: false,
    kind: null,
    id: null,
    version: null,
    title: null,
    summary: null,
    recommendation: null,
    positional: [],
  };
  const copy = [...rest];
  const strFlag = (name, key) => {
    if (copy[0] === name && copy[1] !== undefined) {
      copy.shift();
      out[key] = String(copy.shift());
      return true;
    }
    const p = `${name}=`;
    if (copy[0] && copy[0].startsWith(p)) {
      out[key] = copy[0].slice(p.length);
      copy.shift();
      return true;
    }
    return false;
  };
  while (copy.length) {
    if (copy[0] === "--cwd" && copy[1]) {
      copy.shift();
      out.cwd = String(copy.shift());
      continue;
    }
    if (copy[0] && copy[0].startsWith("--cwd=")) {
      out.cwd = copy.shift().slice("--cwd=".length);
      continue;
    }
    if (copy[0] === "--yes" || copy[0] === "-y") {
      out.yes = true;
      copy.shift();
      continue;
    }
    if (strFlag("--kind", "kind")) continue;
    if (strFlag("--id", "id")) continue;
    if (strFlag("--version", "version")) continue;
    if (strFlag("--title", "title")) continue;
    if (strFlag("--summary", "summary")) continue;
    if (strFlag("--recommendation", "recommendation")) continue;
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

async function promptLine(msg) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  try {
    return await new Promise((resolve) => rl.question(msg, resolve));
  } finally {
    rl.close();
  }
}

function resolveBaseUrl(ctx, config) {
  return (
    ctx?.baseUrl ||
    process.env.SHIP_API_BASE ||
    config?.api?.base_url ||
    "https://ship.elmundi.com"
  ).replace(/\/$/, "");
}

async function cmdDraft(root, args) {
  if (!args.kind || !ALLOWED_KINDS.includes(args.kind)) {
    console.error(`--kind is required and must be one of: ${ALLOWED_KINDS.join(", ")}`);
    process.exit(1);
  }
  if (!args.id) {
    console.error("--id is required");
    process.exit(1);
  }

  let title = args.title;
  let summary = args.summary;

  if ((!title || !summary) && process.stdin.isTTY) {
    if (!title) title = (await promptLine("title: ")).trim();
    if (!summary) summary = (await promptLine("summary: ")).trim();
  }

  if (!title || !summary) {
    console.error("--title and --summary are required (interactive prompts unavailable).");
    process.exit(1);
  }

  const { config } = readConfig(root);
  const fp = createDraft(root, {
    kind: args.kind,
    id: args.id,
    version: args.version,
    title,
    summary,
    recommendation: args.recommendation,
    stack: config.stack || {},
  });
  console.log(fp);
}

function cmdList(root) {
  const drafts = listDrafts(root);
  if (drafts.length === 0) {
    console.log("(no feedback drafts)");
    return;
  }
  for (const fp of drafts) {
    try {
      const { meta } = readDraft(fp);
      const sent = fp.includes(`${path.sep}sent${path.sep}`) ? " [sent]" : "";
      const base = path.basename(fp);
      const ts = (base.match(/^([0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2})/) || [])[1] || "-";
      const kind = meta.kind || "-";
      const id = meta.id || "-";
      const version = meta.version || "-";
      const title = meta.title || "(untitled)";
      console.log(`${ts} ${kind}/${id}@${version} — ${title}${sent}`);
    } catch (e) {
      console.log(`${fp} (invalid: ${e.message})`);
    }
  }
}

function cmdShow(args) {
  const file = args.positional[1];
  if (!file) {
    console.error("usage: shipctl feedback show <draft-file>");
    process.exit(1);
  }
  const resolved = path.resolve(file);
  if (!fs.existsSync(resolved)) {
    console.error(`not found: ${resolved}`);
    process.exit(1);
  }
  console.log(fs.readFileSync(resolved, "utf8"));
}

function cmdEdit(args) {
  const file = args.positional[1];
  if (!file) {
    console.error("usage: shipctl feedback edit <draft-file>");
    process.exit(1);
  }
  const resolved = path.resolve(file);
  if (!fs.existsSync(resolved)) {
    console.error(`not found: ${resolved}`);
    process.exit(1);
  }
  const editor = process.env.EDITOR || process.env.VISUAL;
  if (!editor) {
    console.log(resolved);
    console.error("(hint: set $EDITOR to open drafts automatically)");
    return;
  }
  const [cmd, ...cmdArgs] = editor.split(/\s+/);
  const child = spawn(cmd, [...cmdArgs, resolved], { stdio: "inherit" });
  child.on("exit", (code) => {
    if (code !== 0) process.exit(code || 1);
  });
}

async function cmdSubmit(ctx, root, args) {
  const file = args.positional[1];
  if (!file) {
    console.error("usage: shipctl feedback submit <draft-file>");
    process.exit(1);
  }
  const resolved = path.resolve(file);
  if (!fs.existsSync(resolved)) {
    console.error(`not found: ${resolved}`);
    process.exit(1);
  }

  const { meta, body } = readDraft(resolved);
  const missing = [];
  if (!meta.kind) missing.push("kind");
  if (!meta.id) missing.push("id");
  if (!meta.title) missing.push("title");
  // Summary lives in body (after '**Summary**:'); derive it for validation.
  const summaryMatch = body.match(/\*\*Summary\*\*:\s*([^\n]+)/);
  const summary = summaryMatch ? summaryMatch[1].trim() : "";
  if (!summary) missing.push("summary");
  if (missing.length) {
    console.error(`missing required fields: ${missing.join(", ")}`);
    process.exit(1);
  }

  if (!args.yes && process.stdin.isTTY) {
    const ok = (await promptLine(`submit ${path.basename(resolved)}? [y/N] `)).trim();
    if (!/^y(es)?$/i.test(ok)) {
      console.error("aborted.");
      process.exit(1);
    }
  }

  const { config } = readConfig(root);
  const baseUrl = resolveBaseUrl(ctx, config);

  const stack = config.stack || {};
  const stackStr = `tracker=${stack.tracker || "-"}, ci=${stack.ci || "-"}, agents=${
    Array.isArray(stack.agents) ? stack.agents.join("+") || "-" : "-"
  }, preset=${stack.preset || "-"}`;

  const recommendationMatch = body.match(/\*\*Recommendation\*\*:\s*([^\n]+)/);
  const recommendation = recommendationMatch ? recommendationMatch[1].trim() : "";
  const recommendations = recommendation ? [recommendation] : [];

  const payload = {
    title: meta.title,
    summary,
    recommendations,
    source_context: stackStr,
    artifact: {
      kind: meta.kind,
      id: meta.id,
      version: meta.version || undefined,
    },
  };

  let data;
  try {
    data = await postFeedback(baseUrl, payload);
  } catch (e) {
    console.error(e.message || String(e));
    process.exit(20);
  }

  const issueUrl = data?.issue_url || "(no issue_url in response)";
  console.log(issueUrl);
  if (data?.deduplicated) console.log("(deduplicated: comment added to existing issue)");

  const sentPath = moveDraftToSent(root, resolved);
  console.log(`moved: ${sentPath}`);

  if (
    config.telemetry?.share === true &&
    config.telemetry?.scope?.improvement_drafts === true
  ) {
    try {
      appendEvent(root, {
        type: "feedback.submit",
        anonymous_id: config.telemetry.anonymous_id,
        payload: {
          artifact: {
            kind: meta.kind,
            id: meta.id,
            version: meta.version || null,
          },
          summary,
          suggestion: recommendation || null,
          stack: {
            tracker: stack.tracker || null,
            ci: stack.ci || null,
            agents: stack.agents || [],
            preset: stack.preset || null,
          },
        },
      });
    } catch (e) {
      if (process.env.SHIP_DEBUG === "1") {
        console.error(`[ship:feedback] failed to append telemetry: ${e.message}`);
      }
    }
  }
}

function cmdRemove(args) {
  const file = args.positional[1];
  if (!file) {
    console.error("usage: shipctl feedback remove <draft-file>");
    process.exit(1);
  }
  const resolved = path.resolve(file);
  if (!fs.existsSync(resolved)) {
    console.error(`not found: ${resolved}`);
    process.exit(1);
  }
  removeDraft(resolved);
  console.log(`removed: ${resolved}`);
}

export async function feedbackCommand(ctx, rest) {
  const args = parseArgs(rest);
  const sub = args.positional[0];
  const root = requireShipRoot(args.cwd);
  // ensure drafts dir exists lazily when commands need it
  if (sub === "list" || sub === "draft") fs.mkdirSync(draftsDir(root), { recursive: true });

  switch (sub) {
    case "draft":
      await cmdDraft(root, args);
      return;
    case "list":
      cmdList(root);
      return;
    case "show":
      cmdShow(args);
      return;
    case "edit":
      cmdEdit(args);
      return;
    case "submit":
      await cmdSubmit(ctx, root, args);
      return;
    case "remove":
      cmdRemove(args);
      return;
    default:
      console.error(
        "usage: shipctl feedback <draft|list|show|edit|submit|remove> [...]\n" +
          "  draft  --kind <k> --id <id> [--version <v>] --title '...' --summary '...' [--recommendation '...']\n" +
          "  list\n" +
          "  show|edit|remove <draft-file>\n" +
          "  submit <draft-file> [--yes]",
      );
      process.exit(2);
  }
}
