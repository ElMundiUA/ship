import { apiGet } from "../http.mjs";

/**
 * @param {{ baseUrl: string; json: boolean }} ctx
 * @param {string[]} args
 */
export async function patternsCommand(ctx, args) {
  const [sub, ...rest] = args;
  if (!sub || sub === "help") {
    console.log(`Usage:
  ship patterns list
  ship patterns show <pattern-id>

Global flags: --base-url URL  --json`);
    return;
  }

  if (sub === "list") {
    const data = await apiGet(ctx.baseUrl, "/patterns");
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`${data.description || "Patterns"}\n`);
      for (const p of data.patterns || []) {
        console.log(`- ${p.id}`);
        console.log(`  ${p.title}`);
        console.log(`  path: ${p.path}  tags: ${(p.tags || []).join(", ")}\n`);
      }
    }
    return;
  }

  if (sub === "show") {
    const id = rest[0];
    if (!id) {
      console.error("show: pattern id required.");
      process.exit(1);
    }
    const data = await apiGet(ctx.baseUrl, `/patterns/${encodeURIComponent(id)}`);
    if (ctx.json) console.log(JSON.stringify(data, null, 2));
    else {
      console.log(`# ${data.title} (${data.id})\n`);
      console.log(data.content);
    }
    return;
  }

  console.error(`Unknown patterns subcommand: ${sub}`);
  process.exit(1);
}
