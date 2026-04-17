import { isFile, readText, walk } from "../_fs.mjs";

export const id = "kotlin";
export const kind = "language";

export async function detect(cwd) {
  const evidence = [];
  let strong = false;

  if (isFile(cwd, "build.gradle.kts")) {
    const body = readText(cwd, "build.gradle.kts") || "";
    evidence.push({ type: "file", where: "build.gradle.kts", match: "present" });
    if (/\bkotlin\b/i.test(body)) {
      strong = true;
      evidence.push({
        type: "file",
        where: "build.gradle.kts",
        match: "kotlin plugin referenced",
      });
    }
  }

  const hasGradle =
    isFile(cwd, "build.gradle") ||
    isFile(cwd, "build.gradle.kts") ||
    isFile(cwd, "settings.gradle") ||
    isFile(cwd, "settings.gradle.kts");
  if (hasGradle) {
    const files = walk(cwd, { maxDepth: 4, maxFiles: 400 });
    const kt = files.find((f) => f.endsWith(".kt"));
    if (kt) evidence.push({ type: "file", where: kt, match: "*.kt present" });
    if (kt && !strong) {
      return { present: true, confidence: 0.7, evidence };
    }
  }

  if (strong) return { present: true, confidence: 1, evidence };
  if (evidence.length) return { present: true, confidence: 0.6, evidence };
  return { present: false, confidence: 0, evidence: [] };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
