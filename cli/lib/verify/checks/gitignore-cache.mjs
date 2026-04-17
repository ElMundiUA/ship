import fs from "node:fs";
import path from "node:path";

export const id = "gitignore-cache";
export const category = "local";
export const description = ".gitignore contains .ship/cache/";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const cacheTracked =
    !!(ctx.config && ctx.config.cache && ctx.config.cache.vcs_tracked === true);
  const giPath = path.join(ctx.cwd, ".gitignore");
  if (!fs.existsSync(giPath)) {
    if (cacheTracked) {
      return {
        status: "pass",
        detail: ".gitignore absent but cache.vcs_tracked=true — cache is intentionally committed",
      };
    }
    return {
      status: "warn",
      detail: ".gitignore not found; add `.ship/cache/` to keep cached artifacts out of git",
    };
  }
  const body = fs.readFileSync(giPath, "utf8");
  const lines = new Set(body.split(/\r?\n/).map((l) => l.trim()));
  const listed = lines.has(".ship/cache/") || lines.has(".ship/cache");

  if (cacheTracked && listed) {
    return {
      status: "warn",
      detail:
        ".ship/cache/ listed in .gitignore but cache.vcs_tracked=true — entries will be ignored by git",
    };
  }
  if (cacheTracked) {
    return {
      status: "pass",
      detail: "cache.vcs_tracked=true; .gitignore does not exclude .ship/cache/",
    };
  }
  if (!listed) {
    return {
      status: "warn",
      detail: ".ship/cache/ not listed in .gitignore — add it to avoid committing cached bodies",
    };
  }
  return { status: "pass", detail: ".ship/cache/ listed in .gitignore" };
}
