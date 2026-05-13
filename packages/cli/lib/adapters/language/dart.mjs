import { isFile } from "../_fs.mjs";

export const id = "dart";
export const kind = "language";

export async function detect(cwd) {
  if (isFile(cwd, "pubspec.yaml")) {
    return {
      present: true,
      confidence: 1,
      evidence: [{ type: "file", where: "pubspec.yaml", match: "present" }],
    };
  }
  return { present: false, confidence: 0, evidence: [] };
}

export async function bootstrap() {
  return { todo: true };
}

export async function verify() {
  return { todo: true };
}
