import fs from "node:fs";
import path from "node:path";

export const id = "ci-secrets";
export const category = "network";
export const description = "Every workflow ${{ secrets.X }} reference is declared in .env.example";

const SECRET_RE = /\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}/g;

/**
 * Extract secret names referenced in a gh-actions workflow body.
 */
function extractSecrets(body) {
  const found = new Set();
  let m;
  // Reset regex across calls
  const re = new RegExp(SECRET_RE.source, SECRET_RE.flags);
  while ((m = re.exec(body)) !== null) {
    const name = m[1];
    if (name && name !== "GITHUB_TOKEN") found.add(name);
  }
  return [...found];
}

function parseEnvExample(body) {
  const out = new Set();
  for (const raw of body.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    if (/^[A-Z0-9_]+$/.test(key)) out.add(key);
  }
  return out;
}

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const ci = ctx.config && ctx.config.stack && ctx.config.stack.ci;
  if (ci !== "gh-actions") {
    return { status: "skip", detail: `stack.ci=${ci || "?"} (gh-actions-only check)` };
  }

  const wfDir = path.join(ctx.cwd, ".github", "workflows");
  if (!fs.existsSync(wfDir)) {
    return { status: "skip", detail: ".github/workflows not present" };
  }
  let files;
  try {
    files = fs.readdirSync(wfDir).filter((f) => /\.ya?ml$/.test(f));
  } catch {
    files = [];
  }
  if (!files.length) {
    return { status: "skip", detail: "no workflow files under .github/workflows" };
  }

  const referenced = new Set();
  for (const f of files) {
    const body = fs.readFileSync(path.join(wfDir, f), "utf8");
    for (const s of extractSecrets(body)) referenced.add(s);
  }
  if (!referenced.size) {
    return { status: "pass", detail: `no ${"${{ secrets.* }}"} references in ${files.length} workflow(s)` };
  }

  const envExamplePath = path.join(ctx.cwd, ".env.example");
  const declared = fs.existsSync(envExamplePath)
    ? parseEnvExample(fs.readFileSync(envExamplePath, "utf8"))
    : new Set();
  const missing = [...referenced].filter((s) => !declared.has(s));
  if (!missing.length) {
    return {
      status: "pass",
      detail: `all ${referenced.size} referenced secret(s) declared in .env.example`,
    };
  }
  return {
    status: "warn",
    detail: `secrets referenced in workflows but missing from .env.example: ${missing.join(", ")}`,
    data: { missing, referenced: [...referenced] },
  };
}
