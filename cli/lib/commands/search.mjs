import { apiPost } from "../http.mjs";

/**
 * Vector search over methodology corpus (documentation, prompts, README).
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args query tokens (same line as `ship search …`)
 */
export async function searchCommand(ctx, args) {
  if (!args.length || args[0] === "help" || args[0] === "-h" || args[0] === "--help") {
    console.log(`Usage:
  ship search <query> [--top-k 8]

POST /search on the methodology API (same SHIP_API_BASE as ship pattern/tool/…).

Global flags: --base-url URL  --json`);
    return;
  }

  const qParts = [];
  let topK = 8;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--top-k" && args[i + 1]) {
      topK = Number(args[++i]);
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
}
