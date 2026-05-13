import { isFile, listDir, readText } from "../_fs.mjs";

export const id = "swift";
export const kind = "language";

export async function detect(cwd) {
  const evidence = [];

  if (isFile(cwd, "Package.swift")) {
    evidence.push({ type: "file", where: "Package.swift", match: "present" });
  }

  for (const name of listDir(cwd, ".")) {
    if (name.endsWith(".xcodeproj")) {
      evidence.push({ type: "dir", where: name, match: "xcodeproj" });
      break;
    }
  }

  if (isFile(cwd, "Podfile")) {
    const body = readText(cwd, "Podfile") || "";
    if (/\bswift\b/i.test(body) || /:swift/i.test(body)) {
      evidence.push({ type: "file", where: "Podfile", match: "swift pods" });
    }
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
