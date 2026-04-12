import { apiPost } from "../http.mjs";

/**
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function docsCommand(ctx, args) {
  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  ship docs search <query> [--top-k 8]
  ship docs fetch <repo-relative-path>
  ship docs feedback --title "..." --summary "..." [--recommendation "line"]... [--source-context "..."]

Global flags: --base-url URL  --json`);
    return;
  }

  if (sub === "search") {
    const qParts = [];
    let topK = 8;
    for (let i = 0; i < rest.length; i++) {
      const a = rest[i];
      if (a === "--top-k" && rest[i + 1]) {
        topK = Number(rest[++i]);
        continue;
      }
      qParts.push(a);
    }
    const query = qParts.join(" ").trim();
    if (query.length < 3) {
      console.error("search: query must be at least 3 characters.");
      process.exit(1);
    }
    const data = await apiPost(ctx.baseUrl, "/search", { query, top_k: topK });
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`Query: ${data.query}\n`);
      for (const r of data.results || []) {
        console.log(`- ${r.path}  (chunk ${r.chunk_index}, distance ${r.distance ?? "n/a"})`);
        console.log(`  ${r.snippet}\n`);
      }
    }
    return;
  }

  if (sub === "fetch") {
    const p = rest.join(" ").trim();
    if (!p) {
      console.error("fetch: path required.");
      process.exit(1);
    }
    const data = await apiPost(ctx.baseUrl, "/fetch", { path: p });
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`# ${data.path}\n`);
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
