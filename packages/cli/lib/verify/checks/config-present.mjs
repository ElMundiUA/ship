import fs from "node:fs";
import path from "node:path";
import { validateConfig } from "../../config/schema.mjs";

export const id = "config-present";
export const category = "local";
export const description = ".ship/config.yml exists and validates";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const configPath = path.join(ctx.cwd, ".ship", "config.yml");
  if (!fs.existsSync(configPath)) {
    return {
      status: "fail",
      detail: `missing ${path.relative(ctx.cwd, configPath)} — run 'shipctl config init'`,
    };
  }
  if (!ctx.config) {
    return {
      status: "fail",
      detail: `${path.relative(ctx.cwd, configPath)} could not be parsed`,
    };
  }
  const res = validateConfig(ctx.config);
  if (!res.ok) {
    return {
      status: "fail",
      detail: `${path.relative(ctx.cwd, configPath)} invalid: ${res.errors[0]}`,
      data: { errors: res.errors, warnings: res.warnings },
    };
  }
  return {
    status: "pass",
    detail: `${path.relative(ctx.cwd, configPath)} parsed; schema v${ctx.config.version ?? "?"}`,
    data: { warnings: res.warnings },
  };
}
