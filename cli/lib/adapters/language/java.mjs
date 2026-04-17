import { isFile } from "../_fs.mjs";

export const id = "java";
export const kind = "language";

export async function detect(cwd) {
  const evidence = [];
  if (isFile(cwd, "pom.xml")) {
    evidence.push({ type: "file", where: "pom.xml", match: "present" });
  }
  if (isFile(cwd, "build.gradle")) {
    evidence.push({ type: "file", where: "build.gradle", match: "present" });
  }
  if (isFile(cwd, "build.gradle.kts")) {
    evidence.push({ type: "file", where: "build.gradle.kts", match: "present" });
  }
  if (!evidence.length) return { present: false, confidence: 0, evidence };
  return { present: true, confidence: 1, evidence };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
