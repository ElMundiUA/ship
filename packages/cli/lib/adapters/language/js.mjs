import { isFile, pkgDeps, readJson } from "../_fs.mjs";

export const id = "js";
export const kind = "language";

export async function detect(cwd) {
  if (!isFile(cwd, "package.json")) {
    return { present: false, confidence: 0, evidence: [] };
  }
  const pkg = readJson(cwd, "package.json");
  const deps = pkgDeps(pkg);
  if (deps.typescript || isFile(cwd, "tsconfig.json")) {
    return {
      present: false,
      confidence: 0,
      evidence: [{ type: "file", where: "package.json", match: "typescript present → js skipped" }],
    };
  }
  return {
    present: true,
    confidence: 0.9,
    evidence: [{ type: "file", where: "package.json", match: "present (no typescript)" }],
  };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
