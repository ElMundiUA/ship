import fs from "node:fs";
import path from "node:path";

export const id = "bootstrap-files";
export const category = "local";
export const description = "Bootstrap scaffolding files carry ship-managed markers";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const stack = (ctx.config && ctx.config.stack) || {};
  const preset = stack.preset;
  const ci = stack.ci;
  const tracker = stack.tracker;

  if (preset !== "mobile-app" || ci !== "gh-actions" || tracker !== "linear") {
    return {
      status: "skip",
      detail: `combo ${preset || "?"}+${ci || "?"}+${tracker || "?"} has no bootstrap template`,
    };
  }

  const targets = [
    {
      rel: ".github/workflows/ship-pilot.yml",
      marker: "ship-managed: workflow",
    },
    {
      rel: ".ship/labels.yml",
      marker: "ship-managed: labels",
    },
    {
      rel: ".env.example",
      marker: "--- ship-managed ---",
    },
  ];

  const rows = [];
  let fail = false;
  let warn = false;
  for (const t of targets) {
    const abs = path.join(ctx.cwd, t.rel);
    if (!fs.existsSync(abs)) {
      rows.push({ path: t.rel, status: "fail", detail: "missing" });
      fail = true;
      continue;
    }
    const body = fs.readFileSync(abs, "utf8");
    if (!body.includes(t.marker)) {
      rows.push({
        path: t.rel,
        status: "warn",
        detail: `present but missing marker '${t.marker}'`,
      });
      warn = true;
      continue;
    }
    rows.push({ path: t.rel, status: "pass", detail: "ok" });
  }

  const status = fail ? "fail" : warn ? "warn" : "pass";
  const detail = status === "pass"
    ? `${rows.length} bootstrap files carry ship-managed markers`
    : rows.filter((r) => r.status !== "pass").map((r) => `${r.path}: ${r.detail}`).join("; ");
  return { status, detail, data: { rows } };
}
