import { isFile } from "../_fs.mjs";

export const id = "go";
export const kind = "language";

export async function detect(cwd) {
  if (isFile(cwd, "go.mod")) {
    return {
      present: true,
      confidence: 1,
      evidence: [{ type: "file", where: "go.mod", match: "present" }],
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
