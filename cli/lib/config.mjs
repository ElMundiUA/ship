/**
 * Pull known global flags out of argv; remainder is the subcommand tail.
 * @param {string[]} argv
 */
export function extractGlobalArgv(argv) {
  const out = {
    _: /** @type {string[]} */ ([]),
    baseUrl: (
      process.env.SHIP_API_BASE || "https://ship.elmundi.com/api/methodology"
    ).replace(/\/$/, ""),
    json: false,
    yes: false,
    force: false,
    dryRun: false,
  };
  const copy = [...argv];
  while (copy.length) {
    const a = copy[0];
    if (a === "--json") {
      out.json = true;
      copy.shift();
      continue;
    }
    if (a === "--yes" || a === "-y") {
      out.yes = true;
      copy.shift();
      continue;
    }
    if (a === "--force") {
      out.force = true;
      copy.shift();
      continue;
    }
    if (a === "--dry-run") {
      out.dryRun = true;
      copy.shift();
      continue;
    }
    if (a === "--base-url" && copy[1]) {
      copy.shift();
      out.baseUrl = String(copy.shift()).replace(/\/$/, "");
      continue;
    }
    if (a.startsWith("--base-url=")) {
      out.baseUrl = a.slice("--base-url=".length).replace(/\/$/, "");
      copy.shift();
      continue;
    }
    out._.push(copy.shift());
  }
  return out;
}
