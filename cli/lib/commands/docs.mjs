import { apiPost } from "../http.mjs";

/**
 * Documentation files only: fetch markdown by repo path, retro feedback.
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function docsCommand(ctx, args) {
  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  shipctl docs fetch <repo-relative-path>
  shipctl docs feedback --title "..." --summary "..." [--recommendation "line"]... [--source-context "..."]

Vector search:  shipctl search <query>
Catalog bodies: shipctl pattern|tool|collection fetch <id>

Global flags: --base-url URL  --json`);
    return;
  }

  if (sub === "fetch") {
    const p = rest.join(" ").trim();
    if (!p) {
      console.error("fetch: repo-relative path required (markdown/text under the Ship tree).");
      process.exit(1);
    }
    const data = await apiPost(ctx.baseUrl, "/fetch", { path: p });
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      const title = data.path ?? data.id ?? "document";
      console.log(`# ${title}\n`);
      console.log(data.content);
    }
    return;
  }

  if (sub === "feedback") {
    const opts = parseFeedbackArgs(rest);
    if (opts.title.length < 5 || opts.summary.length < 10) {
      console.error("feedback: --title (min 5) and --summary (min 10) are required.");
      process.exit(1);
    }
    const body = {
      title: opts.title,
      summary: opts.summary,
      recommendations: opts.recommendations,
      source_context: opts.sourceContext || null,
    };
    const data = await apiPost(ctx.baseUrl, "/feedback", body);
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`Created: ${data.issue_url} (#${data.issue_number})`);
      if (data.redactions_applied) console.log(`Redactions applied: ${data.redactions_applied}`);
    }
    return;
  }

  console.error(`Unknown docs subcommand: ${sub}`);
  process.exit(1);
}

/** @param {string[]} rest */
function parseFeedbackArgs(rest) {
  let title = "";
  let summary = "";
  /** @type {string[]} */
  const recommendations = [];
  let sourceContext = "";
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "--title" && rest[i + 1]) {
      title = rest[++i];
      continue;
    }
    if (a === "--summary" && rest[i + 1]) {
      summary = rest[++i];
      continue;
    }
    if (a === "--recommendation" && rest[i + 1]) {
      recommendations.push(rest[++i]);
      continue;
    }
    if (a === "--source-context" && rest[i + 1]) {
      sourceContext = rest[++i];
      continue;
    }
  }
  return { title, summary, recommendations, sourceContext };
}
