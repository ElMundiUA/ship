import { validateConfig } from "../../config/schema.mjs";

export const id = "stack-enums";
export const category = "config";
export const description = "stack.* values are valid enum members";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  if (!ctx.config) {
    return { status: "skip", detail: "no config loaded" };
  }
  const res = validateConfig(ctx.config);
  if (!res.ok) {
    const stackErrors = res.errors.filter((e) => e.startsWith("stack."));
    const display = stackErrors.length ? stackErrors : res.errors;
    return {
      status: "fail",
      detail: display[0],
      data: { errors: res.errors, warnings: res.warnings },
    };
  }
  const warns = (res.warnings || []).filter((w) => w.startsWith("stack."));
  if (warns.length) {
    return { status: "warn", detail: warns[0], data: { warnings: res.warnings } };
  }
  const stack = ctx.config.stack || {};
  return {
    status: "pass",
    detail: `tracker=${stack.tracker || "?"}, ci=${stack.ci || "?"}, preset=${stack.preset || "?"}, language=${stack.language || "?"}, agents=[${(stack.agents || []).join(",") || ""}]`,
  };
}
